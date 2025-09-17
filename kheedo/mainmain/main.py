import os
import sys
import subprocess

# --- Auto-install required packages ---
required = ["stellar-sdk", "bip-utils", "requests", "mnemonic"]
for pkg in required:
    try:
        __import__(pkg.replace("-", "_"))
    except ImportError:
        print(f"📦 Installing {pkg}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])

# --- Imports after install ---
import time
import requests
import threading
from datetime import datetime, timezone
from mnemonic import Mnemonic
from bip_utils import Bip39SeedGenerator, Bip39MnemonicValidator, Bip44, Bip44Coins, Bip44Changes
from stellar_sdk import Keypair, Server, TransactionBuilder, Asset, Network
from typing import Optional
from stellar_sdk.operation import ClaimClaimableBalance

# ---------------- CONFIG ----------------
HORIZON_URL = "https://api.mainnet.minepi.com"
SAFE_WALLET = "MALYJFJ5SVD45FBWN2GT4IW67SEZ3IBOFSBSPUFCWV427NBNLG3PWAAAAAAAACJUHDSOY"
DRY_RUN = False  # Real transactions enabled
# ----------------------------------------

def format_time_remaining(seconds: int) -> str:
    """Format seconds into human-readable countdown (days, hours, minutes, seconds)"""
    if seconds <= 0:
        return "READY NOW! ⚡"
    
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    
    if days > 0:
        return f"{days}d {hours}h {minutes}m {secs}s"
    elif hours > 0:
        return f"{hours}h {minutes}m {secs}s"
    elif minutes > 0:
        return f"{minutes}m {secs}s"
    else:
        return f"{secs}s"

def mnemonic_to_keypair(mnemonic: str):
    """Convert Pi mnemonic to Ed25519 keypair using Pi Network's actual derivation method"""
    from bip_utils import Bip32Slip10Ed25519
    import nacl.signing
    from stellar_sdk import StrKey
    
    # Pi Network uses SLIP-0010 Ed25519 derivation with path m/44'/314159'/0'
    # This is the exact method from the official Pi Network recovery tool
    seed_bytes = Bip39SeedGenerator(mnemonic).Generate()
    private_key_bytes = Bip32Slip10Ed25519.FromSeed(seed_bytes).DerivePath("m/44'/314159'/0'").PrivateKey().Raw().ToBytes()
    
    # Create Ed25519 keypair for Stellar SDK
    kp = Keypair.from_raw_ed25519_seed(private_key_bytes)
    return kp

def get_available_balance(public_key: str) -> float:
    """Query Horizon account balance and calculate spendable amount"""
    url = f"{HORIZON_URL}/accounts/{public_key}"
    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code != 200:
            return 0.0
        data = resp.json()
        
        # Get total balance
        total_balance = 0.0
        for bal in data.get("balances", []):
            if bal["asset_type"] == "native":
                total_balance = float(bal["balance"])
                break
        
        # Calculate base reserve requirements
        # Base account requires 2 base reserves (1 Pi)
        base_reserve = 0.5  # Pi Network uses 0.5 Pi base reserve
        account_reserves = 2 * base_reserve  # 1 Pi minimum
        
        # Add reserves for subentries (trustlines, offers, data entries)
        subentries = len(data.get("signers", [])) - 1  # -1 because master key doesn't count
        subentries += data.get("num_subentries", 0)  # trustlines, offers, data
        subentry_reserves = subentries * base_reserve
        
        total_reserves = account_reserves + subentry_reserves
        spendable = max(0.0, total_balance - total_reserves)
        
        print(f"💰 Total: {total_balance} Pi | Reserved: {total_reserves} Pi | Spendable: {spendable} Pi")
        return spendable
        
    except requests.exceptions.RequestException as e:
        print(f"❌ Error fetching balance: {e}")
        return 0.0

def get_locked_balances(public_key: str):
    """Query claimable balances for locked Pi with balance IDs for claiming"""
    url = f"{HORIZON_URL}/claimable_balances?claimant={public_key}"
    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code != 200:
            return []
        data = resp.json()
    except requests.exceptions.RequestException as e:
        print(f"❌ Error fetching locked balances: {e}")
        return []
    
    locked = []
    for record in data.get("_embedded", {}).get("records", []):
        amt = float(record["amount"])
        balance_id = record["id"]  # Critical for claiming!
        unlock_time = None
        
        for claimant in record.get("claimants", []):
            predicate = claimant.get("predicate", {})
            abs_before = None
            
            # Handle different predicate structures more robustly
            if isinstance(predicate, dict):
                if "not" in predicate and isinstance(predicate["not"], dict) and "abs_before" in predicate["not"]:
                    # Locked until abs_before time (most common for Pi Network)
                    abs_before = predicate["not"]["abs_before"]
                elif "abs_before" in predicate:
                    # Available until abs_before time (less common)
                    abs_before = predicate["abs_before"]
                elif "and" in predicate:
                    # Handle complex predicates with AND conditions
                    and_conditions = predicate["and"]
                    if isinstance(and_conditions, list):
                        for condition in and_conditions:
                            if isinstance(condition, dict) and "not" in condition and "abs_before" in condition.get("not", {}):
                                abs_before = condition["not"]["abs_before"]
                                break
            
            if abs_before:
                try:
                    unlock_time = datetime.fromisoformat(abs_before.replace("Z", "+00:00"))
                except ValueError as e:
                    print(f"⚠️ Could not parse unlock time '{abs_before}': {e}")
                    unlock_time = None
        
        locked.append((amt, unlock_time, balance_id))
    return locked

def lightning_claim_and_forward(kp: Keypair, balance_id: str, to_addr: str, sponsor_kp: Optional[Keypair] = None):
    """ULTRA-FAST: Claim locked Pi and immediately forward it in ONE atomic transaction"""
    server = Server(horizon_url=HORIZON_URL)
    account = server.load_account(kp.public_key)
    PI_NETWORK_PASSPHRASE = "Pi Network"
    
    # Get balance info to know how much we're claiming
    claim_amount = 0
    try:
        balance_info_url = f"{HORIZON_URL}/claimable_balances/{balance_id}"
        resp = requests.get(balance_info_url, timeout=5)
        if resp.status_code == 200:
            balance_data = resp.json()
            claim_amount = float(balance_data["amount"])
            print(f"⚡ LIGHTNING CLAIM & FORWARD: {claim_amount} Pi (Balance ID: {balance_id[:8]}...)")
        else:
            print("⚠️ Could not get balance info, will claim and forward whatever is available...")
    except Exception as e:
        print(f"⚠️ Error getting balance info: {e}, proceeding with claim...")
    
    # Calculate forwarding amount (leave 1% for fees, reserves already handled by get_available_balance)
    if claim_amount > 0:
        # Account for transaction fees and small buffer
        transaction_fee_buffer = max(0.01, claim_amount * 0.01)  # 1% or 0.01 Pi minimum
        forward_amount = max(0, claim_amount - transaction_fee_buffer)
        forward_amount = round(forward_amount, 6)  # Pi precision
        
        if forward_amount <= 0:
            print(f"⚠️ Claim amount {claim_amount} too small to forward after fees")
            return
            
        print(f"💡 Will claim {claim_amount} Pi and forward {forward_amount} Pi (keeping {claim_amount - forward_amount} Pi for fees)")
        
            # Build ATOMIC LIGHTNING transaction: Claim + Forward in ONE transaction
        # ALWAYS use sponsor for fees - never charge main wallet
        if sponsor_kp:
            print("💳 Fee sponsor paying for claim+forward transaction (main wallet pays nothing)")
            # Use sponsor account for transaction fees
            sponsor_account = server.load_account(sponsor_kp.public_key)
            tx = (
                TransactionBuilder(sponsor_account, PI_NETWORK_PASSPHRASE, base_fee=300000)  # Sponsor pays fees
                .append_claim_claimable_balance_op(balance_id=balance_id, source=kp.public_key)  # Main wallet claims
                .append_payment_op(destination=to_addr, asset=Asset.native(), amount=str(forward_amount), source=kp.public_key)  # Main wallet forwards
                .set_timeout(15)
                .build()
            )
        else:
            print("⚠️ WARNING: No sponsor available - main wallet will pay fees")
            tx = (
                TransactionBuilder(account, PI_NETWORK_PASSPHRASE, base_fee=300000)  # Main wallet pays fees
                .append_claim_claimable_balance_op(balance_id=balance_id)  # CLAIM FIRST
                .append_payment_op(destination=to_addr, asset=Asset.native(), amount=str(forward_amount))  # FORWARD IMMEDIATELY
                .set_timeout(15)  # Slightly longer for 2 operations
                .build()
            )
    else:
        # Fallback: Just claim, then forward immediately (when amount unknown)
        print("🔄 Claiming first, will forward within 2 seconds...")
        if sponsor_kp:
            print("💳 Fee sponsor paying for claim transaction (main wallet pays nothing)")
            sponsor_account = server.load_account(sponsor_kp.public_key)
            tx = (
                TransactionBuilder(sponsor_account, PI_NETWORK_PASSPHRASE, base_fee=200000)  # Sponsor pays fees
                .append_claim_claimable_balance_op(balance_id=balance_id, source=kp.public_key)  # Main wallet claims
                .set_timeout(10)
                .build()
            )
        else:
            print("⚠️ WARNING: No sponsor available - main wallet will pay fees")
            tx = (
                TransactionBuilder(account, PI_NETWORK_PASSPHRASE, base_fee=200000)
                .append_claim_claimable_balance_op(balance_id=balance_id)
                .set_timeout(10)
                .build()
            )
    
    # Sign transaction - proper fee sponsorship
    if sponsor_kp:
        tx.sign(sponsor_kp)  # Sponsor signs first (fee source)
        tx.sign(kp)  # Main wallet signs operations
    else:
        tx.sign(kp)  # Main wallet handles everything
    
    if DRY_RUN:
        print("🚧 DRY_RUN: Lightning claim & forward transaction ready")
        print("Transaction XDR:", tx.to_xdr()[:50] + "...")
        return
    
    # SUBMIT ATOMIC LIGHTNING TRANSACTION!
    try:
        resp = server.submit_transaction(tx)
        if resp.get('successful'):
            print(f"🚀 LIGHTNING CLAIM & FORWARD SUCCESS! TX: {resp['hash'][:16]}...")
            if claim_amount > 0:
                print(f"✅ {round(claim_amount - max(0.01, claim_amount * 0.01), 6)} Pi forwarded to safe wallet in atomic transaction!")
            else:
                print("✅ Pi claimed! Forwarding immediately...")
                # IMMEDIATE FORWARD after claim (fallback case)
                time.sleep(1)  # Brief pause for network confirmation
                forward_all(kp, to_addr, sponsor_kp)
        else:
            print(f"❌ Lightning claim failed: {resp.get('extras', {}).get('result_codes', resp)}")
            # Don't call forward_all here as the claim failed
    except Exception as e:
        print(f"❌ Lightning transaction error: {e}")

def build_sponsored_transaction(source_kp: Keypair, sponsor_kp: Optional[Keypair], to_addr: str, amount: str):
    """Build a fee-sponsored transaction where sponsor pays fees"""
    server = Server(horizon_url=HORIZON_URL)
    source_account = server.load_account(source_kp.public_key)
    PI_NETWORK_PASSPHRASE = "Pi Network"
    
    # Build transaction with sponsor as fee source
    tx = (
        TransactionBuilder(source_account, PI_NETWORK_PASSPHRASE, base_fee=100000)
        .append_payment_op(destination=to_addr, asset=Asset.native(), amount=amount)
        .set_timeout(30)
        .build()
    )
    
    # Sign with both source and sponsor
    tx.sign(source_kp)  # Source authorizes the payment
    if sponsor_kp:
        tx.sign(sponsor_kp)  # Sponsor authorizes fee payment
    
    return tx

def forward_all(kp: Keypair, to_addr: str, sponsor_kp: Optional[Keypair] = None):
    """Send all available Pi to safe wallet"""
    server = Server(horizon_url=HORIZON_URL)
    account = server.load_account(kp.public_key)

    bal = get_available_balance(kp.public_key)
    # Now 'bal' is already the spendable amount (reserves already subtracted)
    # Just need to leave some for transaction fees
    transaction_fee = 0.01  # Small buffer for transaction fees
    
    if bal <= transaction_fee:
        print("⚠️ No spendable funds available after accounting for reserves and fees.")
        return
    
    # Use fee sponsor logic if available and needed
    if sponsor_kp and bal < 0.02:  # If balance very low, use sponsor
        amt = bal  # Send entire balance since sponsor pays fees
        print(f"💳 Using fee sponsor - sending ALL {amt} Pi (sponsor pays fees)")
        try:
            tx = build_sponsored_transaction(kp, sponsor_kp, to_addr, str(amt))
        except Exception as e:
            print(f"❌ Fee sponsor transaction failed: {e}")
            return
    else:
        # Normal transaction - keep some for fees
        amt = round(bal * 0.99, 6) if bal > 0.02 else 0
        if amt <= 0:
            print(f"⚠️ Cannot send - need fee sponsor or more balance (current: {bal} Pi)")
            return
        print(f"🚀 Sending {amt} Pi (keeping {round(bal - amt, 6)} Pi for transaction fees)")
        
        PI_NETWORK_PASSPHRASE = "Pi Network"
        tx = (
            TransactionBuilder(account, PI_NETWORK_PASSPHRASE, base_fee=100000)
            .append_payment_op(destination=to_addr, asset=Asset.native(), amount=str(amt))
            .set_timeout(30)
            .build()
        )
        tx.sign(kp)

    if DRY_RUN:
        print("🚧 DRY_RUN active — transaction not sent")
        print("Signed XDR:", tx.to_xdr())
    else:
        resp = server.submit_transaction(tx)
        print("✅ Transaction broadcast:", resp)

def prebuild_lightning_transaction(kp: Keypair, balance_id: str, to_addr: str, sponsor_kp: Optional[Keypair] = None, claim_amount: float = 0):
    """PRE-BUILD lightning transaction for instant submission when unlock happens"""
    server = Server(horizon_url=HORIZON_URL)
    PI_NETWORK_PASSPHRASE = "Pi Network"
    
    # Calculate forwarding amount (same logic as lightning_claim_and_forward)
    if claim_amount > 0:
        transaction_fee_buffer = max(0.01, claim_amount * 0.01)  # 1% or 0.01 Pi minimum
        forward_amount = max(0, claim_amount - transaction_fee_buffer)
        forward_amount = round(forward_amount, 6)  # Pi precision
        
        if forward_amount <= 0:
            return None
            
        # Build ATOMIC LIGHTNING transaction: Claim + Forward in ONE transaction
        if sponsor_kp:
            # Use sponsor account for transaction fees
            sponsor_account = server.load_account(sponsor_kp.public_key)
            tx = (
                TransactionBuilder(sponsor_account, PI_NETWORK_PASSPHRASE, base_fee=300000)  # Sponsor pays fees
                .append_claim_claimable_balance_op(balance_id=balance_id, source=kp.public_key)  # Main wallet claims
                .append_payment_op(destination=to_addr, asset=Asset.native(), amount=str(forward_amount), source=kp.public_key)  # Main wallet forwards
                .set_timeout(15)
                .build()
            )
            # Pre-sign the transaction
            tx.sign(sponsor_kp)  # Sponsor signs first (fee source)
            tx.sign(kp)  # Main wallet signs operations
        else:
            # Build without sponsor
            account = server.load_account(kp.public_key)
            tx = (
                TransactionBuilder(account, PI_NETWORK_PASSPHRASE, base_fee=300000)
                .append_claim_claimable_balance_op(balance_id=balance_id)
                .append_payment_op(destination=to_addr, asset=Asset.native(), amount=str(forward_amount))
                .set_timeout(15)
                .build()
            )
            tx.sign(kp)
        
        return tx
    return None

def instant_submit_transaction(prebuild_tx):
    """INSTANT SUBMIT - No delays, pure speed"""
    if DRY_RUN:
        print("🚧 DRY_RUN: Pre-built lightning transaction ready for instant execution")
        return
    
    server = Server(horizon_url=HORIZON_URL)
    try:
        # INSTANT SUBMISSION - No hesitation!
        resp = server.submit_transaction(prebuild_tx)
        if resp.get('successful'):
            print(f"🚀 INSTANT LIGHTNING SUCCESS! TX: {resp['hash'][:16]}...")
            print("⚡ MILLISECOND EXECUTION ACHIEVED!")
        else:
            print(f"❌ Instant lightning failed: {resp.get('extras', {}).get('result_codes', resp)}")
    except Exception as e:
        print(f"❌ Instant execution error: {e}")

def main():
    print("=== Pi Auto Forwarder ===")
    mnemonic = input("Enter your 24-word Pi passphrase: ").strip()
    kp = mnemonic_to_keypair(mnemonic)

    print("🔑 Public Key:", kp.public_key)
    print("🏦 Safe Wallet:", SAFE_WALLET)
    
    # Fee Sponsor Wallet Setup
    print("\n💳 Fee Sponsor Setup (optional - press Enter to skip)")
    sponsor_mnemonic = input("Enter fee sponsor wallet 24-word passphrase (or Enter to skip): ").strip()
    sponsor_kp = None
    if sponsor_mnemonic:
        try:
            sponsor_kp = mnemonic_to_keypair(sponsor_mnemonic)
            sponsor_balance = get_available_balance(sponsor_kp.public_key)
            print(f"💳 Fee Sponsor Key: {sponsor_kp.public_key}")
            print(f"💰 Sponsor Balance: {sponsor_balance} Pi")
            if sponsor_balance < 0.1:
                print("⚠️ WARNING: Fee sponsor has low balance - may not be able to pay fees!")
        except Exception as e:
            print(f"❌ Invalid sponsor passphrase: {e}")
            sponsor_kp = None
    else:
        print("⏭️ Skipping fee sponsor - transactions will use source wallet for fees")

    while True:
        try:
            avail = get_available_balance(kp.public_key)
            locked = get_locked_balances(kp.public_key)

            # Check if any locked Pi is unlocking within 1 minute - if so, FOCUS MODE
            current_time = datetime.now(timezone.utc)
            unlock_within_minute = False
            for amt, unlock_time, balance_id in locked:
                if unlock_time:
                    delta = (unlock_time - current_time).total_seconds()
                    if delta <= 60:  # Within 1 minute
                        unlock_within_minute = True
                        break

            if avail > 0.01:
                if unlock_within_minute:
                    print(f"🎯 FOCUS MODE: Ignoring {avail} Pi available - locked Pi unlocks within 1 minute!")
                else:
                    print(f"⚡ {avail} Pi available — forwarding now...")
                    forward_all(kp, SAFE_WALLET, sponsor_kp)

            # LIGHTNING CLAIMING LOGIC
            soonest = None
            soonest_balance_id = None
            total_locked_pi = sum(amt for amt, _, _ in locked) if locked else 0
            
            # PRE-BUILT TRANSACTION CACHE for lightning speed
            prebuild_tx = None
            prebuild_balance_id = None
            
            if locked:
                print(f"📊 MONITORING: {len(locked)} locked balance(s) totaling {total_locked_pi} Pi")
            
            for amt, unlock_time, balance_id in locked:
                if unlock_time:
                    delta = (unlock_time - current_time).total_seconds()
                    time_remaining = format_time_remaining(delta)
                    
                    if delta <= 30:  # If unlocking in next 30 seconds, start monitoring closely
                        print(f"🔥 READY TO CLAIM: {amt} Pi unlocking in {time_remaining} - PREPARING LIGHTNING STRIKE!")
                        
                        # PRE-BUILD TRANSACTION for instant execution (when delta <= 2s)
                        if delta <= 2 and prebuild_balance_id != balance_id:
                            try:
                                prebuild_tx = prebuild_lightning_transaction(kp, balance_id, SAFE_WALLET, sponsor_kp, amt)
                                prebuild_balance_id = balance_id
                                print(f"⚡ TRANSACTION PRE-BUILT - READY FOR INSTANT STRIKE!")
                            except Exception as e:
                                print(f"⚠️ Pre-build failed: {e}")
                                
                        if delta <= 0:  # ATTEMPT #1: Fire immediately when unlock time reached
                            print(f"⚡⚡⚡ UNLOCK TIME REACHED! FIRING ATTEMPT #1: {amt} Pi")
                            print(f"🎯 ATTEMPT #1: Time-based strike (unlock time reached)")
                            
                            # Fire attempt #1 in background thread - NO WAITING!
                            def attempt_1():
                                try:
                                    if prebuild_tx and prebuild_balance_id == balance_id:
                                        # ULTRA-FAST: Submit pre-built transaction instantly
                                        instant_submit_transaction(prebuild_tx)
                                    else:
                                        # Fallback to normal method
                                        lightning_claim_and_forward(kp, balance_id, SAFE_WALLET, sponsor_kp)
                                except Exception as e:
                                    print(f"❌ Attempt #1 failed: {e}")
                            
                            thread1 = threading.Thread(target=attempt_1)
                            thread1.start()  # Fire immediately, don't wait for response
                            print(f"🚀 Attempt #1 fired! Continuing monitoring for detection-based attempt...")
                            
                        # ATTEMPT #2: Check if monitoring detects Pi is actually available for claiming
                        try:
                            current_locked = get_locked_balances(kp.public_key)
                            pi_detected_ready = False
                            for check_amt, check_unlock, check_balance_id in current_locked:
                                if check_balance_id == balance_id and check_unlock:
                                    check_delta = (check_unlock - datetime.now(timezone.utc)).total_seconds()
                                    if check_delta <= -1:  # Pi is definitely detected as unlocked
                                        pi_detected_ready = True
                                        break
                            
                            if not pi_detected_ready:
                                # Check if Pi completely disappeared from locked balances (claimed by someone else or us)
                                balance_still_exists = False
                                for check_amt, check_unlock, check_balance_id in current_locked:
                                    if check_balance_id == balance_id:
                                        balance_still_exists = True
                                        break
                                
                                if not balance_still_exists:
                                    print(f"🎯 ATTEMPT #2: Pi balance no longer exists - likely claimed!")
                                    continue  # Skip to next balance
                            
                            if pi_detected_ready:
                                print(f"🔥 DETECTION-BASED TRIGGER! Pi confirmed ready by monitoring!")
                                print(f"🎯 ATTEMPT #2: Availability-based strike (Pi detected ready)")
                                
                                # Fire attempt #2 in background thread - NO WAITING!
                                def attempt_2():
                                    try:
                                        lightning_claim_and_forward(kp, balance_id, SAFE_WALLET, sponsor_kp)
                                    except Exception as e:
                                        print(f"❌ Attempt #2 failed: {e}")
                                
                                thread2 = threading.Thread(target=attempt_2)
                                thread2.start()  # Fire immediately, don't wait for response
                                print(f"⚡ Attempt #2 fired based on Pi detection!")
                        except Exception as e:
                            print(f"⚠️ Detection check failed: {e}")
                    
                    if delta > 0:
                        print(f"🔒 {amt} Pi unlocks at {unlock_time.strftime('%Y-%m-%d %H:%M:%S UTC')} ⏳ ({time_remaining} remaining)")
                        if soonest is None or unlock_time < soonest:
                            soonest = unlock_time
                            soonest_balance_id = balance_id

            if soonest:
                sleep_secs = max(0, (soonest - current_time).total_seconds())
                next_unlock_countdown = format_time_remaining(sleep_secs)
                
                if sleep_secs <= 2:  # MAXIMUM PRECISION MODE: Final 2 seconds
                    print(f"⚡⚡⚡ MAXIMUM PRECISION MODE: 0.01s intervals - {next_unlock_countdown} until STRIKE!")
                    time.sleep(0.01)  # 100x per second - millisecond precision!
                elif sleep_secs <= 10:  # HYPER-SPEED MODE: 10-3 seconds
                    print(f"🚀 HYPER-SPEED MODE: 0.05s intervals - {next_unlock_countdown} until STRIKE!")
                    time.sleep(0.05)  # 20x per second - maximum possible aggression!
                elif sleep_secs <= 60:  # If unlocking in next minute, ultra-aggressive mode
                    print(f"🔥 ULTRA SPEED MODE: 0.1s intervals - {next_unlock_countdown} until STRIKE!")
                    time.sleep(0.1)  # 10x per second for ultra precision
                elif sleep_secs <= 300:  # If unlocking in next 5 minutes, fast mode
                    print(f"⚡ FAST MODE: 5s intervals - {next_unlock_countdown} until unlock")
                    time.sleep(5)  # 5 second checking for manual operation
                else:
                    # For distant unlocks - exit since bot is run manually before unlock
                    print(f"⏸ Unlock too far away ({next_unlock_countdown}). Exiting - restart bot closer to unlock time!")
                    exit(0)  # Exit completely instead of long waits
            else:
                print("⏸ No locked balances found. Exiting - restart when Pi is locked!")
                exit(0)  # Exit instead of waiting

        except Exception as e:
            print("❌ Error:", e)
            time.sleep(30)

if __name__ == "__main__":
    main()

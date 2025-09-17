# Pi Auto Forwarder

## Overview
A Python console application that automatically monitors and forwards Pi Network cryptocurrency from user wallets to a designated safe wallet. The system tracks both available and locked Pi balances, automatically transferring available funds and monitoring locked funds until they become claimable. Built on Pi Network's Stellar-based blockchain infrastructure with robust error handling and safety features.

**Current Status**: Successfully imported and configured for Replit environment - fully operational and ready for use

## Recent Changes
- **Sept 17, 2025**: GitHub import setup completed for Replit environment
  - Installed Python 3.12 with all required dependencies via UV package manager
  - Configured "Pi Auto Forwarder" console workflow for continuous monitoring
  - Set up VM deployment configuration for production hosting
  - Created Python-specific .gitignore file
  - Application successfully running and awaiting user input

## User Preferences
Preferred communication style: Simple, everyday language.

## System Architecture

### Core Architecture
- **Single-file application**: All functionality contained in main.py for simplicity and portability
- **Auto-dependency management**: Automatically installs required packages (stellar-sdk, bip-utils, requests, mnemonic) on first run
- **Continuous monitoring loop**: Designed for long-running background operation with periodic balance checks

### Wallet and Cryptography
- **BIP44 derivation**: Converts 24-word Pi Network mnemonic phrases to Ed25519 keypairs using Pi Network's specific derivation method
- **Stellar SDK integration**: Uses Stellar SDK for blockchain interactions with Pi Network's mainnet
- **Key management**: Secure handling of private keys derived from user-provided mnemonics

### Transaction Processing
- **Dual balance tracking**: Monitors both available balances (ready for transfer) and locked balances (time-locked funds)
- **Automatic forwarding**: Transfers available Pi to configured safe wallet address
- **Locked balance monitoring**: Continuously tracks locked funds and processes them when they unlock
- **Safety mechanisms**: Includes dry-run mode for testing without actual transactions

### Error Handling and Safety
- **Dry-run mode**: Default safety flag prevents accidental live transactions during testing
- **Robust error recovery**: Handles network failures, API timeouts, and transaction errors gracefully
- **Time-based countdown**: Provides human-readable time remaining for locked balance unlocking

### Configuration Management
- **Centralized constants**: Key configuration values (Horizon URL, safe wallet address, dry-run flag) defined at module level
- **Environment flexibility**: Can be easily configured for different networks or wallet addresses

## External Dependencies

### Blockchain Infrastructure
- **Pi Network Horizon API**: Main blockchain interaction through `https://api.mainnet.minepi.com`
- **Stellar SDK**: Core blockchain functionality for transaction building and submission
- **Pi Network Mainnet**: Target blockchain network for all operations

### Python Libraries
- **stellar-sdk**: Primary blockchain interaction library
- **bip-utils**: BIP44 hierarchical deterministic wallet functionality
- **mnemonic**: BIP39 mnemonic phrase validation and processing
- **requests**: HTTP client for API interactions
- **PyNaCl**: Cryptographic operations (auto-installed dependency)

### Runtime Environment
- **Replit VM**: Designed for deployment on Replit's virtual machine infrastructure
- **Continuous execution**: Requires persistent runtime environment for monitoring locked balances
- **Interactive console**: Requires user input for mnemonic phrase entry at startup
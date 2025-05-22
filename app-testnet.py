import os
import pandas as pd
from web3 import Web3
from web3.middleware import geth_poa_middleware
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv
import logging
import time
import json
import math
import requests
import glob

logger = logging.getLogger()
logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
handler = RotatingFileHandler('batch_transfer.log', maxBytes=10*1024*1024, backupCount=5)
handler.setFormatter(formatter)
logger.addHandler(handler)

# =================== Configuration ================= #
CONFIG = {
    'CHAIN_ID': 17000,  # Replace other ID default Holesky
    'RPC_URLS': [
        'https://1rpc.io/holesky',
        'https://ethereum-holesky-rpc.publicnode.com',
        'https://holesky.drpc.org'
    ],
    'TOKENS': [
        {'address': '0x1265ace75c199a531b7b1cd2a9666f434325d1e8', 'amount': 1.1, 'symbol': 'WETH'},
        {'address': '0x15b1121c947d1806e32c4c00e41c60bdf1b35e26', 'amount': 1.1, 'symbol': 'WBTC'}
    ],
    'BATCH_SIZE': 100, # default 100 address per rawTransfer
    'TELEGRAM': {
        'ENABLED': True,  # False without notify Telegram
        'BOT_TOKEN': 'your_bot_token_here',
        'CHAT_ID': 'your_chat_id_here'
    }
}

# Path undiplicated the address
PROCESSED_FILE = 'processed_addresses.json'

load_dotenv()
PRIVATE_KEYS = os.getenv('PRIVATE_KEYS', '').split(',')
if not PRIVATE_KEYS or not PRIVATE_KEYS[0]:
    logging.error("PRIVATE_KEYS not found in the .env")
    raise ValueError("PRIVATE_KEYS not found in the .env")

# Default ABI ERC-20 ERC-721 and metadata for batch transfer airdrop
ABI = [
    {
        "inputs": [
            {"internalType": "address[]", "name": "recipients", "type": "address[]"},
            {"internalType": "uint256", "name": "amount", "type": "uint256"}
        ],
        "name": "AirdropBatch",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "name",
        "outputs": [{"internalType": "string", "name": "", "type": "string"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "symbol",
        "outputs": [{"internalType": "string", "name": "", "type": "string"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"internalType": "uint8", "name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "totalSupply",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [{"internalType": "address", "name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    }
]

def connect_to_rpc(rpc_url):
    try:
        w3 = Web3(Web3.HTTPProvider(rpc_url))
        w3.middleware_onion.inject(geth_poa_middleware, layer=0)
        if w3.is_connected():
            logging.info(f"Connected to RPC node: {rpc_url}")
            return w3
        else:
            logging.warning(f"Failed connected to RPC node: {rpc_url}")
            return None
    except Exception as e:
        logging.warning(f"Error on RPC node {rpc_url}: {str(e)}")
        return None

# RPC manager for connection fallback/reconnect
class Web3Manager:
    def __init__(self, rpc_urls):
        self.rpc_urls = rpc_urls
        self.current_rpc_index = 0
        self.w3 = self.connect()

    def connect(self):
        for i in range(len(self.rpc_urls)):
            w3 = connect_to_rpc(self.rpc_urls[self.current_rpc_index])
            if w3:
                return w3
            self.current_rpc_index = (self.current_rpc_index + 1) % len(self.rpc_urls)
        logging.error("Failed connected to all RPC node")
        raise Exception("Failed connected to all RPC node")

    def get_web3(self):
        if not self.w3.is_connected():
            logging.warning("Disconnect RPC, trying to reconnect")
            self.w3 = self.connect()
        return self.w3

    def switch_rpc(self):
        self.current_rpc_index = (self.current_rpc_index + 1) % len(self.rpc_urls)
        logging.info(f"Switch to other RPC: {self.rpc_urls[self.current_rpc_index]}")
        self.w3 = self.connect()
        return self.w3

web3_manager = Web3Manager(CONFIG['RPC_URLS'])
# Auto detect address for own SENDER any token
w3 = web3_manager.get_web3()
PRIVATE_KEY = PRIVATE_KEYS[0]
SENDER_ADDRESS = w3.eth.account.from_key(PRIVATE_KEY).address
logging.info(f"Sender address: {SENDER_ADDRESS}")

# Auto detect contract address in use metadata ERC20/721
contracts = []
for i, token in enumerate(CONFIG['TOKENS']):
    try:
        contract = w3.eth.contract(address=Web3.to_checksum_address(token['address']), abi=ABI)
        name = contract.functions.name().call()
        symbol = contract.functions.symbol().call()
        decimals = contract.functions.decimals().call()
        total_supply = contract.functions.totalSupply().call()
        amount = Web3.to_wei(token['amount'], 'ether') if decimals == 18 else int(token['amount'] * (10 ** decimals))
        contracts.append({
            'contract': contract,
            'address': token['address'],
            'name': name,
            'symbol': symbol,
            'decimals': decimals,
            'total_supply': total_supply,
            'amount': amount
        })
        logging.info(f"Contract {symbol}: Name={name}, Decimal={decimals}, Total Supply={total_supply / (10 ** decimals)} {symbol}, Balance per address={token['amount']} {symbol}")
    except Exception as e:
        logging.error(f"Error load contract {token['address']}: {str(e)}")
        raise

# Auto detect for file CSV
def detect_csv_file():
    csv_files = glob.glob('*.csv')
    if not csv_files:
        logging.error("Not found any file CSV at currenct directory")
        raise ValueError("Not found any file CSV")
    if len(csv_files) > 1:
        logging.warning(f"Has found any file CSV: {csv_files}. Used it: {csv_files[0]}")
    return csv_files[0]

# Generic null address for burn/mint/events
def is_valid_address(address):
    try:
        return Web3.is_address(address) and address != '0x0000000000000000000000000000000000000000'
    except:
        return False

# Auto detect read CSV for address in column/name/0x others
def detect_address_column(df):
    for col in df.columns:
        sample = df[col].dropna().head(10).tolist()
        if sample and all(isinstance(s, str) and is_valid_address(s) for s in sample if s):
            return col
    address_keywords = ['address', 'airdrop', 'holder', 'wallet', 'eth']
    for col in df.columns:
        if any(keyword.lower() in col.lower() for keyword in address_keywords):
            return col
    logging.warning(f"Tidak ditemukan kolom alamat, menggunakan kolom pertama: {df.columns[0]}")
    return df.columns[0]

# Muat alamat yang sudah diproses
def load_processed_addresses():
    try:
        if os.path.exists(PROCESSED_FILE):
            with open(PROCESSED_FILE, 'r') as f:
                return set(json.load(f))
        return set()
    except Exception as e:
        logging.error(f"Error membaca file riwayat: {str(e)}")
        return set()

# Simpan alamat yang sudah diproses
def save_processed_addresses(addresses):
    try:
        with open(PROCESSED_FILE, 'w') as f:
            json.dump(list(addresses), f)
    except Exception as e:
        logging.error(f"Error menyimpan file riwayat: {str(e)}")

# Baca dan validasi CSV
def load_addresses(csv_file):
    try:
        df = pd.read_csv(csv_file)
        address_column = detect_address_column(df)
        logging.info(f"Menggunakan kolom alamat: {address_column}")
        
        addresses = df[address_column].dropna().drop_duplicates().tolist()
        valid_addresses = [Web3.to_checksum_address(addr) for addr in addresses if is_valid_address(addr)]
        
        processed_addresses = load_processed_addresses()
        remaining_addresses = [addr for addr in valid_addresses if addr not in processed_addresses]
        
        logging.info(f"Ditemukan {len(valid_addresses)} alamat valid, {len(remaining_addresses)} belum diproses")
        return remaining_addresses
    except Exception as e:
        logging.error(f"Error membaca CSV: {str(e)}")
        raise

# Buat batch
def create_batches(addresses, batch_size):
    total_batches = math.ceil(len(addresses) / batch_size)
    batches = [addresses[i:i + batch_size] for i in range(0, len(addresses), batch_size)]
    for i, batch in enumerate(batches, 1):
        if len(batch) != batch_size and i != len(batches):
            logging.warning(f"Batch {i} hanya memiliki {len(batch)} alamat, diharapkan {batch_size}")
    return batches, total_batches

# Kirim notifikasi Telegram
def send_telegram_notification(message):
    if not CONFIG['TELEGRAM']['ENABLED']:
        return
    try:
        bot_token = CONFIG['TELEGRAM']['BOT_TOKEN']
        chat_id = CONFIG['TELEGRAM']['CHAT_ID']
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {'chat_id': chat_id, 'text': message}
        response = requests.post(url, json=payload)
        if response.status_code != 200:
            logging.warning(f"Gagal mengirim notifikasi Telegram: {response.text}")
    except Exception as e:
        logging.warning(f"Error mengirim notifikasi Telegram: {str(e)}")

# Kirim batch transfer
def send_batch_transfer(contract, batch, amount, contract_name, max_retries=3):
    processed_addresses = load_processed_addresses()
    retries = 0
    while retries < max_retries:
        try:
            w3 = web3_manager.get_web3()
            # Estimasi gas
            gas_estimate = contract.functions.AirdropBatch(batch, amount).estimate_gas({'from': SENDER_ADDRESS})
            gas = int(gas_estimate * 1.01)  # Buffer 10%
            # Bangun transaksi
            tx = contract.functions.AirdropBatch(batch, amount).build_transaction({
                'from': SENDER_ADDRESS,
                'nonce': w3.eth.get_transaction_count(SENDER_ADDRESS),
                'gas': gas,
                'gasPrice': w3.eth.gas_price * 2,
                'chainId': CONFIG['CHAIN_ID']
            })

            # Tandatangani transaksi
            signed_tx = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)

            # Kirim transaksi
            tx_hash = w3.eth.send_raw_transaction(signed_tx.rawTransaction)
            logging.info(f"Transaksi {contract_name} dikirim untuk batch dengan {len(batch)} alamat: {tx_hash.hex()}")

            # Tunggu konfirmasi
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=600)
            if receipt.status == 1:
                logging.info(f"Transaksi {contract_name} berhasil: {tx_hash.hex()}")
                # Tambahkan alamat ke riwayat
                processed_addresses.update(batch)
                save_processed_addresses(processed_addresses)
                # Kirim notifikasi Telegram
                send_telegram_notification(f"Batch {contract_name} selesai: {tx_hash.hex()}")
                return tx_hash.hex()
            else:
                logging.error(f"Transaksi {contract_name} gagal: {tx_hash.hex()}")
                raise Exception("Transaksi gagal")
        
        except Exception as e:
            retries += 1
            logging.warning(f"Coba ulang {retries}/{max_retries} untuk {contract_name}: {str(e)}")
            if retries == max_retries:
                logging.error(f"Gagal setelah {max_retries} coba untuk {contract_name}: {str(e)}")
                raise
            web3_manager.switch_rpc()
            global contracts
            for c in contracts:
                c['contract'] = web3_manager.get_web3().eth.contract(address=c['address'], abi=ABI)
            time.sleep(10)

def main():
    try:
        # Periksa saldo kontrak (hanya untuk logging)
        w3 = web3_manager.get_web3()
        for contract_info in contracts:
            balance = contract_info['contract'].functions.balanceOf(contract_info['address']).call()
            logging.info(f"Saldo {contract_info['symbol']}: {balance / (10 ** contract_info['decimals'])} {contract_info['symbol']}")

        # Deteksi file CSV
        csv_file = detect_csv_file()
        logging.info(f"Menggunakan file CSV: {csv_file}")

        # Muat alamat dari CSV
        addresses = load_addresses(csv_file)
        processed_count = len(load_processed_addresses())
        logging.info(f"Sudah memproses {processed_count} alamat. Tersisa {len(addresses)} alamat.")
        
        if not addresses:
            logging.info("Tidak ada alamat baru untuk diproses")
            return
        
        # Buat batch
        batches, total_batches = create_batches(addresses, CONFIG['BATCH_SIZE'])
        logging.info(f"Membuat {len(batches)} batch dari {len(addresses)} alamat, total batch: {total_batches}")
        
        # Proses setiap batch
        for i, batch in enumerate(batches, processed_count // CONFIG['BATCH_SIZE'] + 1):
            logging.info(f"Memproses batch {i}/{total_batches} dengan {len(batch)} alamat")
            
            for contract_info in contracts:
                tx_hash = send_batch_transfer(
                    contract_info['contract'],
                    batch,
                    contract_info['amount'],
                    contract_info['symbol']
                )
                logging.info(f"Batch {i} {contract_info['symbol']} selesai: {tx_hash}")
                time.sleep(100 if i == 1 else 150)  # Jeda bervariasi per token
            
        logging.info("Semua batch transfer selesai")
    
    except Exception as e:
        logging.error(f"Error di main: {str(e)}")
        raise

if __name__ == "__main__":
    main()

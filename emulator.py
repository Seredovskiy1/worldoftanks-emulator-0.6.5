import socket
import struct
import select
import os
import time
import threading
import pickle
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_OAEP, Blowfish

LOGIN_PORT  = 20016
BASEAPP_PORT = 20017

# { token_bytes : { 'bf_key': bytes, 'addr': (ip,port) } }
active_sessions = {}

print("[*] Завантажуємо RSA ключі...")
try:
    with open("private_key.pem", "rb") as f:
        private_key = RSA.import_key(f.read())
except Exception as e:
    print(f"[!] ПОМИЛКА: Немає private_key.pem! {e}"); exit()

# ── RSA / parse ──────────────────────────────────────────────────────────────

def rsa_decrypt_oaep(blob, key):
    cipher = PKCS1_OAEP.new(key)
    try: return cipher.decrypt(blob)
    except: pass
    try: return cipher.decrypt(blob[::-1])
    except: return None

def parse_logon_params(data):
    try:
        p = 0
        p += 1                                      # flags byte
        ul = data[p]; p += 1
        user = data[p:p+ul].decode('ascii','ignore'); p += ul
        pl = data[p]; p += 1
        pwd  = data[p:p+pl].decode('ascii','ignore'); p += pl
        kl = data[p]; p += 1
        key  = data[p:p+kl]
        return user, pwd, key
    except: return None, None, None

def find_rsa_block(data):
    for start in range(15, 30):
        if start + 256 > len(data): continue
        dec = rsa_decrypt_oaep(data[start:start+256], private_key)
        if dec and dec[0] in (0x00, 0x01):
            return dec
    return None

# ── BigWorld Blowfish (XOR-chain ECB) ────────────────────────────────────────

def bw_bf_encrypt(plaintext, key):
    """BigWorld custom Blowfish: block[i] = BF( plain[i] XOR plain[i-1] )"""
    BS = 8
    pad = (BS - len(plaintext) % BS) % BS
    data = plaintext + b'\x00' * pad
    c = Blowfish.new(key, Blowfish.MODE_ECB)
    prev = None; out = b''
    for i in range(0, len(data), BS):
        blk = data[i:i+BS]
        mod = blk if prev is None else bytes(a^b for a,b in zip(blk, prev))
        out += c.encrypt(mod)
        prev = blk
    return out

def bw_bf_decrypt_alt(ciphertext, key):
    """Alternate chaining attempt: XOR with previous ciphertext block."""
    BS = 8
    if len(ciphertext) % BS != 0:
        return None
    c = Blowfish.new(key, Blowfish.MODE_ECB)
    prev_cipher = None
    out = b''
    for i in range(0, len(ciphertext), BS):
        enc_blk = ciphertext[i:i+BS]
        mod = c.decrypt(enc_blk)
        plain_blk = mod if prev_cipher is None else bytes(a ^ b for a, b in zip(mod, prev_cipher))
        out += plain_blk
        prev_cipher = enc_blk
    return out

def bw_bf_decrypt(ciphertext, key):
    """Reverse of BigWorld custom Blowfish XOR-chain ECB."""
    BS = 8
    if len(ciphertext) % BS != 0:
        return None
    c = Blowfish.new(key, Blowfish.MODE_ECB)
    prev_plain = None
    out = b''
    for i in range(0, len(ciphertext), BS):
        enc_blk = ciphertext[i:i+BS]
        mod = c.decrypt(enc_blk)
        plain_blk = mod if prev_plain is None else bytes(a ^ b for a, b in zip(mod, prev_plain))
        out += plain_blk
        prev_plain = plain_blk
    return out

ENCRYPTION_MAGIC = 0xdeadbeef

# Mercury Packet flags (little-endian on this client build)
PACKET_FLAG_HAS_REQUESTS = 0x0001
PACKET_FLAG_HAS_ACKS = 0x0004
PACKET_FLAG_ON_CHANNEL = 0x0008
PACKET_FLAG_IS_FRAGMENT = 0x0020
PACKET_FLAG_HAS_SEQUENCE_NUMBER = 0x0040
PACKET_FLAG_HAS_CUMULATIVE_ACK = 0x0400
PACKET_SEQ_MASK = 0x0FFFFFFF

def bw_encrypt_packet(packet: bytes, key: bytes) -> bytes:
    """Encrypt full Mercury packet like BigWorld EncryptionFilter::send()."""
    BS = 8
    base_len = len(packet) + 4  # + ENCRYPTION_MAGIC
    wastage = ((BS - ((base_len + 1) % BS)) % BS) + 1

    # footer: [pad bytes][magic uint32][wastage uint8]
    footer = (b'\x00' * (wastage - 1)) + struct.pack('<I', ENCRYPTION_MAGIC) + bytes([wastage])
    plain = packet + footer
    return bw_bf_encrypt(plain, key)

def bw_decrypt_packet(packet: bytes, key: bytes):
    """Decrypt full Mercury packet and strip BigWorld encryption footer."""
    for clear in (bw_bf_decrypt(packet, key), bw_bf_decrypt_alt(packet, key)):
        if not clear or len(clear) < 5:
            continue

        wastage = clear[-1]
        footer_size = wastage + 4  # wastage includes wastage byte itself, + magic(4)
        if wastage < 1 or wastage > 8 or footer_size > len(clear):
            continue

        # In BigWorld packet footer layout, magic is always right before the
        # final wastage byte (independent of wastage value): [...][magic(4)][w]
        magic_pos = len(clear) - 5
        magic_le = struct.unpack('<I', clear[magic_pos:magic_pos + 4])[0]
        magic_be = struct.unpack('>I', clear[magic_pos:magic_pos + 4])[0]
        if magic_le != ENCRYPTION_MAGIC and magic_be != ENCRYPTION_MAGIC:
            continue

        return clear[:-footer_size]

    return None

def split_packet_body(packet: bytes):
    """Split Mercury packet into message-body part and parsed footer info."""
    if len(packet) < 2:
        return packet, 0, None, None

    flags = struct.unpack('<H', packet[:2])[0]
    idx = len(packet)
    seq = None
    cumulative_ack = None

    # Footer strip order must match PacketReceiver::processFilteredPacket()
    if flags & PACKET_FLAG_HAS_CUMULATIVE_ACK:
        if idx < 4:
            return packet, flags, None, None
        cumulative_ack = struct.unpack('<I', packet[idx - 4:idx])[0]
        idx -= 4

    if flags & PACKET_FLAG_HAS_ACKS:
        if idx < 1:
            return packet, flags, None, cumulative_ack
        ack_count = packet[idx - 1]
        idx -= 1
        ack_bytes = ack_count * 4
        if idx < ack_bytes:
            return packet, flags, None, cumulative_ack
        idx -= ack_bytes

    if flags & PACKET_FLAG_HAS_SEQUENCE_NUMBER:
        if idx < 4:
            return packet, flags, None, cumulative_ack
        seq = struct.unpack('<I', packet[idx - 4:idx])[0]
        idx -= 4

    if flags & PACKET_FLAG_HAS_REQUESTS:
        if idx < 2:
            return packet, flags, seq, cumulative_ack
        idx -= 2

    if flags & PACKET_FLAG_IS_FRAGMENT:
        if idx < 8:
            return packet, flags, seq, cumulative_ack
        idx -= 8

    return packet[:idx], flags, seq, cumulative_ack

def update_in_seq_state(sess: dict, seq: int):
    """Track next expected incoming seq for cumulative ACK generation."""
    if seq is None:
        return

    in_seq = sess.setdefault('in_seq_at', 0)
    buffered = sess.setdefault('in_seq_buffered', set())

    if seq == in_seq:
        in_seq = (in_seq + 1) & PACKET_SEQ_MASK
        while in_seq in buffered:
            buffered.remove(in_seq)
            in_seq = (in_seq + 1) & PACKET_SEQ_MASK
        sess['in_seq_at'] = in_seq
    elif seq > in_seq:
        buffered.add(seq)

def build_channel_packet(messages: bytes, sess: dict, reliable=False) -> bytes:
    # BigWorld тримає ДВА окремих лічильники sequence:
    #   - reliable пакети    → channel.useNextSequenceID()
    #   - non-reliable       → networkInterface.getNextSequenceID()
    # Клієнтський reliable-window перевіряє послідовність ТІЛЬКИ для reliable.
    # Якщо змішати — reliable пакети йдуть з пропусками і клієнт їх буферизує
    # назавжди, не виконуючи. Тримаємо два окремих counter'а.
    if reliable:
        out_seq = sess.setdefault('out_channel_seq', 0)
        sess['out_channel_seq'] = (out_seq + 1) & PACKET_SEQ_MASK
    else:
        out_seq = sess.setdefault('out_nub_seq', 0)
        sess['out_nub_seq'] = (out_seq + 1) & PACKET_SEQ_MASK

    in_seq = sess.setdefault('in_seq_at', 0)

    flags = (PACKET_FLAG_ON_CHANNEL |
             PACKET_FLAG_HAS_SEQUENCE_NUMBER |
             PACKET_FLAG_HAS_CUMULATIVE_ACK)
    if reliable:
        flags |= 0x0010

    pkt = struct.pack('<H', flags)
    pkt += messages
    pkt += struct.pack('<I', out_seq)
    pkt += struct.pack('<I', in_seq)
    return pkt

def build_channel_ack_packet(sess: dict) -> bytes:
    return build_channel_packet(b'', sess, reliable=False)

BASEAPP_EXT_FIXED_LENGTHS = {
    1: 4,
    2: 25,
    3: 34,
    4: 28,
    5: 37,
    6: 1,
    7: 5,
    8: 0,
    10: 1,
    11: 4,
    12: 1,
}

def iter_baseapp_ext_messages(body: bytes):
    pos = 2
    while pos < len(body):
        msg = body[pos]
        start = pos
        pos += 1

        if msg == 9 or msg >= 128:
            if pos + 2 > len(body):
                yield msg, body[pos:], start
                return
            size = struct.unpack('<H', body[pos:pos + 2])[0]
            pos += 2
            payload = body[pos:pos + size]
            pos += size
            yield msg, payload, start
            continue

        size = BASEAPP_EXT_FIXED_LENGTHS.get(msg)
        if size is None:
            yield msg, body[pos:], start
            return

        payload = body[pos:pos + size]
        pos += size
        yield msg, payload, start

# ── Mercury packet helpers ────────────────────────────────────────────────────

def make_reply(reply_id: bytes, payload: bytes) -> bytes:
    """0xFF reply: flags(2) + 0xFF(1) + len(4) + reply_id(4) + payload"""
    body = reply_id + payload
    return b'\x00\x00\xff' + struct.pack('<I', len(body)) + body

def make_bundle(messages: bytes) -> bytes:
    """Plain server→client bundle (no requests, no encryption)"""
    return b'\x00\x00' + messages

def msg_fixed(msg_id: int, payload: bytes) -> bytes:
    return bytes([msg_id]) + payload

def msg_varlen(msg_id: int, payload: bytes) -> bytes:
    return bytes([msg_id]) + struct.pack('<H', len(payload)) + payload

# ── ClientInterface MsgIDs ───────────────────────────────────────────────────
#  0x00 authenticate          fixed  4 B  (uint32 key)
#  0x01 bandwidthNotification fixed  4 B  (uint32 bps)
#  0x02 updateFrequencyNotif  fixed  1 B  (uint8  hertz)
#  0x03 setGameTime           fixed  4 B  (uint32 gameTime)
#  0x04 resetEntities         fixed  1 B  (bool   keepPlayer)
#  0x05 createBasePlayer      varlen 2 B  EntityID(4)+EntityTypeID(2)+props
#  0x80+N entityMessage       varlen 2 B  EntityID(4)+methodArgs

ACCOUNT_ENTITY_TYPE = 0   # Account is first entity type in WoT entities list
PLAYER_ENTITY_ID    = 1   # any non-zero EntityID for our player
SPACE_ID            = 1   # arbitrary SpaceID for hangar

# WoT 0.6.5 Account ClientMethods indexes — отримані парсером Account.def +
# Chat.def + AccountEditor.def + TransactionUser.def (інтерфейс Chat додає
# ОДИН client-method `onChatAction` ПЕРЕД методами Account, тому всі
# Account-методи зміщені на +1).
#
#   idx=2  msgID=0x82  onCmdResponse        (INT16 reqID, INT16 resultID)
#   idx=3  msgID=0x83  onCmdResponseExt     (INT16 reqID, INT16 resultID, STRING ext)
#   idx=16 msgID=0x90  showGUI              (STRING ctx)
#   idx=21 msgID=0x95  update               (STRING diff)
ACCOUNT_SHOWGUI_MSG_ID         = 0x90
ACCOUNT_ONCMDRESPONSE_MSG_ID   = 0x82
ACCOUNT_ONCMDRESPONSEEXT_MSG_ID = 0x83
ACCOUNT_UPDATE_MSG_ID          = 0x95

# Account exposed BaseMethods (client → server):
#   idx=3 msgID=0xc3  doCmdStr      (INT16 reqID, INT16 cmd, STRING)
#   idx=4 msgID=0xc4  doCmdInt3     (INT16 reqID, INT16 cmd, INT32, INT32, INT32)
#   idx=5 msgID=0xc5  doCmdInt4     (INT16 reqID, INT16 cmd, 4 x INT32)
#   idx=6 msgID=0xc6  doCmdInt2Str  (INT16 reqID, INT16 cmd, INT32, INT32, STRING)
#   idx=7 msgID=0xc7  doCmdIntArr   (INT16 reqID, INT16 cmd, INT32 array)
ACCOUNT_DOCMD_MSG_IDS = {0xc3, 0xc4, 0xc5, 0xc6, 0xc7}


def bw_pack_int(value: int) -> bytes:
    if value >= 0xFF:
        return b'\xff' + bytes((value & 0xFF, (value >> 8) & 0xFF, (value >> 16) & 0xFF))
    return bytes((value,))


def bw_pack_string(data: bytes) -> bytes:
    return bw_pack_int(len(data)) + data

def build_init_bundle(session_key_int: int) -> bytes:
    msgs = b''

    # 0x00 authenticate – server proves it knows the session key
    msgs += msg_fixed(0x00, struct.pack('<I', session_key_int))

    # 0x01 bandwidthNotification – 64 KB/s
    msgs += msg_fixed(0x01, struct.pack('<I', 64000))

    # 0x02 updateFrequencyNotification – 10 Hz
    msgs += msg_fixed(0x02, struct.pack('<B', 10))

    # 0x03 setGameTime – tick 0
    msgs += msg_fixed(0x03, struct.pack('<I', 0))

    # 0x04 resetEntities – don't keep player
    msgs += msg_fixed(0x04, struct.pack('<B', 0))

    # 0x05 createBasePlayer
    #   EntityID(4 LE) + EntityTypeID(2 LE) + base+client property stream
    #   Account має 3 BASE_AND_CLIENT властивості (порядок як у Account.def):
    #     1. name           (STRING)
    #     2. normalizedName (STRING)
    #     3. serverSettings (PYTHON  →  pickled dict)
    #   serverSettings ПОВИНЕН містити ключ 'vivoxDomain', інакше
    #   Account.onBecomePlayer падає з 'NoneType is unsubscriptable'
    #   (див. World_of_Tanks/res/scripts/client/Account.py:142).
    server_settings_pickle = (
        b"(dp0\n"
        b"S'vivoxDomain'\np1\nS''\np2\ns"
        b"S'vivoxIssuer'\np3\nS''\np4\ns"
        b"S'voipDomain'\np5\nS''\np6\ns"
        b"S'serverUTC'\np7\nL0L\ns"
        b"."
    )
    props = b''
    props += bw_pack_string(b'qwerty')               # name
    props += bw_pack_string(b'qwerty')               # normalizedName
    props += bw_pack_string(server_settings_pickle)  # serverSettings (PYTHON)

    cbp_payload = struct.pack('<I', PLAYER_ENTITY_ID) + \
                  struct.pack('<H', ACCOUNT_ENTITY_TYPE) + \
                  props
    msgs += msg_varlen(0x05, cbp_payload)

    # Account.showGUI(ctx)  — entityMessage, msgID=0x90
    # Тригерить WindowsManager.showLobby → CommonPage.processLobby.
    showgui_ctx = b"(dp0\nS'databaseID'\np1\nL1L\nsS'serverUTC'\np2\nL0L\ns."
    em = struct.pack('<I', PLAYER_ENTITY_ID) + bw_pack_string(showgui_ctx)
    msgs += msg_varlen(ACCOUNT_SHOWGUI_MSG_ID, em)

    return msgs


def build_oncmdrespext(req_id: int, result_id: int, ext_pickle: bytes) -> bytes:
    """Account.onCmdResponseExt(reqID, resultID, ext) — entityMessage 0x83."""
    em = struct.pack('<I', PLAYER_ENTITY_ID)
    em += struct.pack('<hh', req_id, result_id)  # INT16, INT16
    em += bw_pack_string(ext_pickle)             # STRING (PYTHON pickle)
    return msg_varlen(ACCOUNT_ONCMDRESPONSEEXT_MSG_ID, em)


def parse_doCmd_request(msg_id: int, payload: bytes):
    """Витягнути (reqID, cmd) з doCmd* пакету. Args починаються з INT16+INT16."""
    if len(payload) < 4:
        return None, None
    req_id, cmd = struct.unpack_from('<hh', payload, 0)
    return req_id, cmd


def make_full_sync_pickle() -> bytes:
    """Повний (full-sync) diff для Account._update. Без 'prevRev' →
    isFullSync=True → клієнт чистить __cache і застосовує наш diff.

    inventory[itemTypeIdx] МУСИТЬ містити sub-dicts, інакше
    InventoryParser.parseVehicles крашить з 'NoneType is unsubscriptable'
    на data['compDescr'] / data['crew'] etc.
    ITEM_TYPE_INDICES (з res/scripts/common/items/__init__.py):
      1=vehicle, 2=vehicleChassis, 3=vehicleTurret, 4=vehicleGun,
      5=vehicleEngine, 6=vehicleFuelTank, 7=vehicleRadio, 8=tankman,
      9=optionalDevice, 10=shell, 11=equipment.
    """
    diff = {
        'rev': 0,
        'inventory': {
            1: {  # vehicle
                'compDescr': {}, 'shellsLayout': {}, 'shells': {},
                'crew': {}, 'repair': {}, 'eqsLayout': {}, 'eqs': {},
                'settings': {}, 'lock': {},
            },
            2: {}, 3: {}, 4: {}, 5: {}, 6: {}, 7: {},
            8: {'compDescr': {}, 'vehicle': {}},   # tankman
            9: {}, 10: {}, 11: {},
        },
        'cache': {'vehsLock': {}},
        'stats': {
            'credits': 0, 'gold': 0, 'freeXP': 0,
            'vehTypeXP': {}, 'tankmen': {}, 'eliteVehicles': (),
            'dossier': '', 'clanInfo': None,
        },
        'shop': {'rev': 0},
        'account': {'attrs': 0, 'premiumExpiryTime': 0},
    }
    return pickle.dumps(diff, protocol=0)


_CACHED_SYNC_PICKLE = None


def get_sync_pickle() -> bytes:
    global _CACHED_SYNC_PICKLE
    if _CACHED_SYNC_PICKLE is None:
        _CACHED_SYNC_PICKLE = make_full_sync_pickle()
    return _CACHED_SYNC_PICKLE


def handle_account_doCmd(sock, addr, sess, msg_id: int, payload: bytes):
    """Універсальний "fake-success" responder на будь-який doCmd*.
    На КОЖЕН запит шлемо повний sync diff — Inventory/Stats/Trader/Shop
    наповнять свої кеші порожніми dict'ами і processLobby не впаде."""
    req_id, cmd = parse_doCmd_request(msg_id, payload)
    if req_id is None:
        return
    print(f"    [doCmd] msg=0x{msg_id:02x} reqID={req_id} cmd={cmd}")

    msg = build_oncmdrespext(req_id, 0, get_sync_pickle())
    pkt = build_channel_packet(msg, sess, reliable=True)
    pkt = bw_encrypt_packet(pkt, sess['bf_key'])
    try:
        sock.sendto(pkt, addr)
    except Exception:
        return
    print(f"    [>] onCmdResponseExt(req={req_id}, res=0, ext=full_sync_diff)")

def send_init_bundle(sock, addr, sess):
    if sess.get('init_sent'):
        return

    token = sess.get('token')
    if not token:
        return

    session_key_int = struct.unpack('<I', token)[0]
    init_msgs = build_init_bundle(session_key_int)
    init_pkt = build_channel_packet(init_msgs, sess, reliable=True)
    init_pkt = bw_encrypt_packet(init_pkt, sess['bf_key'])
    sock.sendto(init_pkt, addr)
    sess['init_sent'] = True
    print(f"[>] Init bundle: authenticate+bandwidth+setGameTime"
          f"+resetEntities+createBasePlayer+showGUI(0x90)")

    if not sess.get('tick_started'):
        start_tick_thread(sock, addr, sess)
        sess['tick_started'] = True

# ── tickSync loop ─────────────────────────────────────────────────────────────

tick_counter = 0

def start_tick_thread(sock, addr, sess=None):
    """Send tickSync (MsgID 0x0D, 1 byte tick counter) every 100 ms."""
    global tick_counter
    def _loop():
        global tick_counter
        while True:
            time.sleep(0.1)
            tick_byte = tick_counter & 0xFF
            tick_counter += 1
            pkt = msg_fixed(0x0D, struct.pack('<B', tick_byte))
            if sess:
                pkt = build_channel_packet(pkt, sess, reliable=False)
                pkt = bw_encrypt_packet(pkt, sess['bf_key'])
            else:
                pkt = make_bundle(pkt)
            try: sock.sendto(pkt, addr)
            except: break
    t = threading.Thread(target=_loop, daemon=True)
    t.start()

# ── LoginApp handler ──────────────────────────────────────────────────────────

def handle_loginapp(sock):
    try:
        data, addr = sock.recvfrom(4096)
    except ConnectionResetError:
        return
    print(f"\n[+] LoginApp: {len(data)} байт від {addr}")
    reply_id = data[5:9]

    dec = find_rsa_block(data)
    if not dec:
        print("[-] RSA decrypt failed"); return

    username, password, bf_key = parse_logon_params(dec)
    if not username: return

    token = os.urandom(4)
    active_sessions[token] = {'bf_key': bf_key, 'addr': None}
    print(f"[+] Логін: '{username}' | Token: {token.hex()}")

    # LoginReplyRecord: Mercury::Address(ip 4B + port 2B + salt 2B) + sessionKey(4B) = 12 B
    record  = socket.inet_aton('127.0.0.1')
    record += struct.pack('>H', BASEAPP_PORT)
    record += b'\x00\x00'            # salt (Mercury::Address.salt)
    record += token
    enc = bw_bf_encrypt(record, bf_key)

    resp_payload = reply_id + b'\x01' + enc   # status=LOGGED_ON(0x01)
    resp = b'\x00\x00\xff' + struct.pack('<I', len(resp_payload)) + resp_payload
    sock.sendto(resp, addr)
    print(f"[>] LOGGED_ON відправлено, клієнт іде на BaseApp:{BASEAPP_PORT}")

# ── BaseApp handler ───────────────────────────────────────────────────────────

baseapp_clients = {}   # addr → session info/state

def handle_baseapp(sock):
    try:
        data, addr = sock.recvfrom(4096)
    except ConnectionResetError:
        return
    if len(data) < 4: return

    # Після успішного login клієнт може перейти на зашифровані пакети.
    sess_for_addr = baseapp_clients.get(addr)
    decrypted = False
    if sess_for_addr:
        dec = bw_decrypt_packet(data, sess_for_addr['bf_key'])
        if dec:
            data = dec
            decrypted = True
            if len(data) < 4:
                return

    body = data
    flags = struct.unpack('<H', data[:2])[0]
    in_seq = None

    if decrypted:
        body, flags, in_seq, _ = split_packet_body(data)
        if len(body) < 3:
            return

        # Просто оновлюємо in_seq state. ACK піггібекаємо в наступний
        # вихідний пакет (init bundle або tickSync) — окремий ACK-пакет
        # збиває власну нумерацію out_seq.
        if sess_for_addr and (flags & PACKET_FLAG_ON_CHANNEL) and (in_seq is not None):
            update_in_seq_state(sess_for_addr, in_seq)

    msg_id = body[2]

    # ── baseAppLogin (MsgID 0x00) ──────────────────────────────────────────
    if (not decrypted) and body[:2] == b'\x01\x00' and msg_id == 0x00 and len(body) >= 15:
        reply_id       = body[5:9]
        token_received = body[11:15]
        print(f"\n[+] BaseApp: baseAppLogin від {addr} | Token: {token_received.hex()}")

        sess = active_sessions.get(token_received)
        if not sess:
            # Спробуємо знайти за будь-яким ключем (на випадок різних зсувів)
            for k, v in active_sessions.items():
                if token_received in (k, k[::-1]):
                    sess = v; break

        if not sess:
            print(f"[-] Невідомий Token! Дамп: {data.hex()}")
            return

        sess['addr'] = addr
        sess['token'] = token_received
        # Кожен новий baseAppLogin → новий channel, скидаємо усі counters,
        # інакше повторне підключення піде з seq=N+1, а клієнт чекає 0.
        sess['init_sent'] = False
        sess['tick_started'] = False
        sess['in_seq_at'] = 0
        sess['in_seq_buffered'] = set()
        sess['out_channel_seq'] = 0
        sess['out_nub_seq'] = 0
        baseapp_clients[addr] = sess

        # 1) Відповідь на baseAppLogin: просто echo token як SessionKey
        reply = make_reply(reply_id, token_received)
        reply = bw_encrypt_packet(reply, sess['bf_key'])
        sock.sendto(reply, addr)
        print(f"[>] baseAppLogin Reply відправлено")

        # 2) Init відправимо після першого enableEntities/authenticate від клієнта.

    # ── інші повідомлення від клієнта ──────────────────────────────────────
    else:
        if decrypted and sess_for_addr:
            messages = list(iter_baseapp_ext_messages(body))
            summary = ", ".join(f"0x{m:02x}({len(p)}B)" for m, p, _ in messages)
            print(f"[<] BaseAppExt: {summary or 'none'} | flags=0x{flags:04x} seq={in_seq} body={body.hex()}")

            if any(m in (0x01, 0x0A) for m, _, _ in messages) and not sess_for_addr.get('init_sent'):
                send_init_bundle(sock, addr, sess_for_addr)
                return

            # Будь-який наступний пакет після init – дампимо + відповідаємо
            # на exposed Account base-methods (doCmdStr/Int3/Int4/Int2Str/IntArr).
            for m, p, _ in messages:
                print(f"    [post-init] msg=0x{m:02x} payload={p.hex()}")
                if m in ACCOUNT_DOCMD_MSG_IDS:
                    handle_account_doCmd(sock, addr, sess_for_addr, m, p)
            return

        print(f"[!!!] BaseApp пакет MsgID=0x{msg_id:02x} ({len(body)}B): {body.hex()}")

# ── Main loop ─────────────────────────────────────────────────────────────────

def main():
    login_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    login_sock.bind(('0.0.0.0', LOGIN_PORT))

    base_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    base_sock.bind(('0.0.0.0', BASEAPP_PORT))

    print(f"[*] WoT 0.6.5 Emulator | LoginApp:{LOGIN_PORT} | BaseApp:{BASEAPP_PORT}")
    print("[*] Запускай гру і тисни Connect!\n")

    while True:
        readable, _, _ = select.select([login_sock, base_sock], [], [], 1.0)
        for s in readable:
            if s is login_sock:
                try:
                    handle_loginapp(s)
                except ConnectionResetError:
                    continue
            elif s is base_sock:
                try:
                    handle_baseapp(s)
                except ConnectionResetError:
                    continue

if __name__ == '__main__':
    main()

import socket
import struct
import select
import os
import time
import datetime
import threading
import pickle
import zlib
import builtins
import math
import pymysql
import pymysql.cursors
import hashlib
import random
import re
import contextlib
import zipfile
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_OAEP, Blowfish
from server_core.config import get_value, load_config, resolve_existing_path


_raw_print = print
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
CONFIG = load_config(ROOT_DIR)


def get_client_roots():
    roots = [ROOT_DIR]
    configured = get_value(CONFIG, 'paths.client_root')
    if configured:
        roots.append(configured)
    try:
        roots.append(os.path.abspath(os.path.join(ROOT_DIR, os.pardir, 'World_of_Tanks')))
        roots.append(os.path.abspath(os.path.join(ROOT_DIR, os.pardir, os.pardir, 'World_of_Tanks')))
    except Exception:
        pass
    try:
        roots.append(os.path.expanduser('~/Desktop/World_of_Tanks'))
    except Exception:
        pass
    roots.append(r'C:\Users\qwerty\Desktop\World_of_Tanks')
    return [r for r in roots if r]



def _fix_mojibake_text(value):
    if not isinstance(value, str):
        return value
    if not any(marker in value for marker in ('Р', 'С', 'вЂ', 'в”')):
        return value
    try:
        return value.encode('cp1251').decode('utf-8')
    except UnicodeError:
        return value


def print(*args, **kwargs):
    _raw_print(*[_fix_mojibake_text(arg) for arg in args], **kwargs)


builtins.print = print

LOGIN_PORT  = int(get_value(CONFIG, 'server.login_port', 20016))
BASEAPP_PORT = int(get_value(CONFIG, 'server.baseapp_port', 20017))
PUBLIC_HOST = str(get_value(CONFIG, 'server.public_host', '26.108.162.225'))
BASEAPP_INIT_DELAY_SECONDS = float(get_value(
    CONFIG, 'server.baseapp_init_delay_seconds', 0.05))

# { token_bytes : { 'bf_key': bytes, 'addr': (ip,port) } }
active_sessions = {}
db_lock = threading.RLock()
battle_lock = threading.RLock()
queue_lock = threading.RLock()

DB_HOST     = str(get_value(CONFIG, 'database.host', '127.0.0.1'))
DB_PORT     = int(get_value(CONFIG, 'database.port', 3306))
DB_USER     = str(get_value(CONFIG, 'database.user', 'root'))
DB_PASSWORD = str(get_value(CONFIG, 'database.password', ''))
DB_NAME     = str(get_value(CONFIG, 'database.name', 'wot_emulator'))

DEFAULT_CREDITS = int(get_value(CONFIG, 'account.default_credits', 1000000000))
DEFAULT_GOLD    = int(get_value(CONFIG, 'account.default_gold', 1000000))
DEFAULT_FREE_XP = int(get_value(CONFIG, 'account.default_free_xp', 1000000))
DEFAULT_SLOTS   = int(get_value(CONFIG, 'account.default_slots', 200))
DEFAULT_BERTHS  = int(get_value(CONFIG, 'account.default_berths', 50))
UNLOCK_ALL_VEHICLES = bool(get_value(CONFIG, 'account.unlock_all_vehicles', False))

NATION_NAMES = ('ussr', 'germany', 'usa', 'china', 'france', 'uk', 'japan')
active_battle_accounts = {}
matchmaking_queue = []
matchmaking_timer = None
next_battle_id = 1
next_visual_shot_id = 1
battle_tick_started = False
battle_tick_sock = None
SERVER_RUNTIME = None


def runtime_call_later(delay, callback):
    if SERVER_RUNTIME is not None:
        return SERVER_RUNTIME.call_later(delay, callback)
    timer = threading.Timer(delay, callback)
    timer.daemon = True
    timer.start()
    return timer


def runtime_sendto(sock, data, addr):
    if SERVER_RUNTIME is not None:
        return SERVER_RUNTIME.sendto(sock, data, addr)
    return sock.sendto(data, addr)

PRIVATE_KEY_PATH = resolve_existing_path(
    ROOT_DIR, get_value(CONFIG, 'server.private_key_path', 'private_key.pem'),
    'private_key.pem')

print("[*] Р—Р°РІР°РЅС‚Р°Р¶СѓС”РјРѕ RSA РєР»СЋС‡С–...")
try:
    with open(PRIVATE_KEY_PATH, "rb") as f:
        private_key = RSA.import_key(f.read())
except Exception as e:
    print(f"[!] РџРћРњРР›РљРђ: РќРµРјР°С” private_key.pem! {e}"); exit()

# в”Ђв”Ђ RSA / parse в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

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

def normalize_login_name(username: str) -> str:
    username = (username or '').strip()
    if '@' in username:
        username = username.split('@')[0]
    username = ''.join(ch for ch in username if ch.isalnum() or ch in ('_', '-', '.'))
    return username[:24] or 'player'

def password_hash(password: str) -> str:
    return hashlib.sha256((password or '').encode('utf-8')).hexdigest()

def db_connect(use_db: bool = True):
    kwargs = dict(
        host=DB_HOST, port=DB_PORT,
        user=DB_USER, password=DB_PASSWORD,
        charset='utf8mb4', autocommit=True,
        cursorclass=pymysql.cursors.Cursor,
    )
    if use_db:
        kwargs['database'] = DB_NAME
    return pymysql.connect(**kwargs)


SCHEMA_STATEMENTS = (
    "CREATE TABLE IF NOT EXISTS accounts ("
    " id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,"
    " username VARCHAR(32) NOT NULL,"
    " email VARCHAR(255) NULL DEFAULT NULL,"
    " normalized_name VARCHAR(32) NOT NULL,"
    " password_hash CHAR(64) NOT NULL,"
    " created_at DATETIME NOT NULL,"
    " last_login DATETIME NOT NULL,"
    " credits BIGINT NOT NULL DEFAULT 1000000000,"
    " gold BIGINT NOT NULL DEFAULT 1000000,"
    " free_xp BIGINT NOT NULL DEFAULT 1000000,"
    " slots INT NOT NULL DEFAULT 200,"
    " berths INT NOT NULL DEFAULT 50,"
    " premium_expire_at BIGINT NOT NULL DEFAULT 0,"
    " attrs BIGINT NOT NULL DEFAULT 0,"
    " clan_db_id BIGINT NOT NULL DEFAULT 0,"
    " is_admin TINYINT NOT NULL DEFAULT 0,"
    " PRIMARY KEY (id),"
    " UNIQUE KEY uniq_username (username),"
    " UNIQUE KEY uniq_email (email),"
    " UNIQUE KEY uniq_normalized_name (normalized_name)"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci",

    "CREATE TABLE IF NOT EXISTS account_unlocks ("
    " account_id BIGINT UNSIGNED NOT NULL,"
    " item_compact_descr BIGINT NOT NULL,"
    " PRIMARY KEY (account_id, item_compact_descr)"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",

    "CREATE TABLE IF NOT EXISTS account_elite_vehicles ("
    " account_id BIGINT UNSIGNED NOT NULL,"
    " vehicle_compact_descr BIGINT NOT NULL,"
    " PRIMARY KEY (account_id, vehicle_compact_descr)"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",

    "CREATE TABLE IF NOT EXISTS account_double_xp_vehicles ("
    " account_id BIGINT UNSIGNED NOT NULL,"
    " vehicle_compact_descr BIGINT NOT NULL,"
    " PRIMARY KEY (account_id, vehicle_compact_descr)"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",

    "CREATE TABLE IF NOT EXISTS battles ("
    " id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,"
    " arena_type_id INT NOT NULL,"
    " queue_type INT NOT NULL DEFAULT 0,"
    " created_at DATETIME NOT NULL,"
    " finished_at DATETIME NULL,"
    " winner_team TINYINT NULL,"
    " finish_reason TINYINT NULL,"
    " PRIMARY KEY (id),"
    " KEY idx_battles_created_at (created_at)"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",

    "CREATE TABLE IF NOT EXISTS battle_entries ("
    " battle_id BIGINT UNSIGNED NOT NULL,"
    " account_id BIGINT UNSIGNED NOT NULL,"
    " vehicle_inv_id INT NOT NULL,"
    " team TINYINT NOT NULL DEFAULT 0,"
    " joined_at DATETIME NOT NULL,"
    " PRIMARY KEY (battle_id, account_id),"
    " KEY idx_entries_account (account_id)"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",

    "CREATE TABLE IF NOT EXISTS battle_results ("
    " id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,"
    " battle_id BIGINT UNSIGNED NOT NULL,"
    " account_id BIGINT UNSIGNED NOT NULL,"
    " vehicle_inv_id INT NOT NULL,"
    " is_winner TINYINT NOT NULL DEFAULT 0,"
    " frags INT NOT NULL DEFAULT 0,"
    " damage_dealt INT NOT NULL DEFAULT 0,"
    " damage_received INT NOT NULL DEFAULT 0,"
    " shots INT NOT NULL DEFAULT 0,"
    " hits INT NOT NULL DEFAULT 0,"
    " life_time_sec INT NOT NULL DEFAULT 0,"
    " credits_earned INT NOT NULL DEFAULT 0,"
    " xp_earned INT NOT NULL DEFAULT 0,"
    " free_xp_earned INT NOT NULL DEFAULT 0,"
    " finished_at DATETIME NOT NULL,"
    " PRIMARY KEY (id),"
    " UNIQUE KEY uniq_battle_account (battle_id, account_id),"
    " KEY idx_results_account (account_id)"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",

    "CREATE TABLE IF NOT EXISTS dossier ("
    " account_id BIGINT UNSIGNED NOT NULL,"
    " total_battles INT NOT NULL DEFAULT 0,"
    " wins INT NOT NULL DEFAULT 0,"
    " losses INT NOT NULL DEFAULT 0,"
    " draws INT NOT NULL DEFAULT 0,"
    " frags INT NOT NULL DEFAULT 0,"
    " damage_dealt BIGINT NOT NULL DEFAULT 0,"
    " damage_received BIGINT NOT NULL DEFAULT 0,"
    " shots INT NOT NULL DEFAULT 0,"
    " hits INT NOT NULL DEFAULT 0,"
    " max_xp INT NOT NULL DEFAULT 0,"
    " max_damage INT NOT NULL DEFAULT 0,"
    " max_frags INT NOT NULL DEFAULT 0,"
    " total_xp BIGINT NOT NULL DEFAULT 0,"
    " total_credits BIGINT NOT NULL DEFAULT 0,"
    " last_battle_at DATETIME NULL,"
    " PRIMARY KEY (account_id)"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",

    "CREATE TABLE IF NOT EXISTS tankmen ("
    " inv_id INT UNSIGNED NOT NULL AUTO_INCREMENT,"
    " account_id BIGINT UNSIGNED NOT NULL,"
    " vehicle_inv_id INT NULL,"
    " slot_idx TINYINT UNSIGNED NULL,"
    " nation_id TINYINT UNSIGNED NOT NULL,"
    " vehicle_type_id SMALLINT UNSIGNED NOT NULL,"
    " role_id TINYINT UNSIGNED NOT NULL,"
    " role_level TINYINT UNSIGNED NOT NULL DEFAULT 100,"
    " is_female TINYINT UNSIGNED NOT NULL DEFAULT 0,"
    " is_premium TINYINT UNSIGNED NOT NULL DEFAULT 0,"
    " first_name_id SMALLINT UNSIGNED NOT NULL,"
    " last_name_id SMALLINT UNSIGNED NOT NULL,"
    " icon_id SMALLINT UNSIGNED NOT NULL,"
    " free_xp INT NOT NULL DEFAULT 0,"
    " skills VARCHAR(64) NOT NULL DEFAULT '',"
    " last_skill_level TINYINT UNSIGNED NOT NULL DEFAULT 0,"
    " created_at DATETIME NOT NULL,"
    " PRIMARY KEY (inv_id),"
    " KEY idx_tankmen_account (account_id),"
    " KEY idx_tankmen_vehicle (account_id, vehicle_inv_id)"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",

    "CREATE TABLE IF NOT EXISTS account_consumables ("
    " account_id BIGINT UNSIGNED NOT NULL,"
    " compact_descr INT UNSIGNED NOT NULL,"
    " quantity INT NOT NULL DEFAULT 0,"
    " PRIMARY KEY (account_id, compact_descr)"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",

    "CREATE TABLE IF NOT EXISTS account_optional_devices ("
    " account_id BIGINT UNSIGNED NOT NULL,"
    " compact_descr INT UNSIGNED NOT NULL,"
    " quantity INT NOT NULL DEFAULT 0,"
    " PRIMARY KEY (account_id, compact_descr)"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",

    "CREATE TABLE IF NOT EXISTS vehicle_consumable_slots ("
    " account_id BIGINT UNSIGNED NOT NULL,"
    " vehicle_inv_id INT NOT NULL,"
    " slot_idx TINYINT UNSIGNED NOT NULL,"
    " compact_descr INT UNSIGNED NOT NULL DEFAULT 0,"
    " PRIMARY KEY (account_id, vehicle_inv_id, slot_idx)"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",

    "CREATE TABLE IF NOT EXISTS vehicle_optional_device_slots ("
    " account_id BIGINT UNSIGNED NOT NULL,"
    " vehicle_inv_id INT NOT NULL,"
    " slot_idx TINYINT UNSIGNED NOT NULL,"
    " compact_descr INT UNSIGNED NOT NULL DEFAULT 0,"
    " PRIMARY KEY (account_id, vehicle_inv_id, slot_idx)"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",

    "CREATE TABLE IF NOT EXISTS vehicle_ammo_layouts ("
    " account_id BIGINT UNSIGNED NOT NULL,"
    " vehicle_inv_id INT NOT NULL,"
    " slot_idx TINYINT UNSIGNED NOT NULL,"
    " shell_compact_descr INT UNSIGNED NOT NULL,"
    " quantity INT NOT NULL DEFAULT 0,"
    " PRIMARY KEY (account_id, vehicle_inv_id, slot_idx)"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
)


def init_database():
    with db_lock:
        try:
            conn = db_connect(use_db=True)
        except pymysql.err.OperationalError as e:
            if e.args and e.args[0] == 1049:
                bootstrap = db_connect(use_db=False)
                try:
                    with bootstrap.cursor() as cur:
                        cur.execute(
                            "CREATE DATABASE IF NOT EXISTS `%s` "
                            "DEFAULT CHARACTER SET utf8mb4 "
                            "DEFAULT COLLATE utf8mb4_unicode_ci" % DB_NAME)
                    print(f"[*] MySQL: created database `{DB_NAME}`")
                finally:
                    bootstrap.close()
                conn = db_connect(use_db=True)
            else:
                raise
        try:
            with conn.cursor() as cur:
                for stmt in SCHEMA_STATEMENTS:
                    cur.execute(stmt)
                migrate_database_schema(cur)
            print(f"[*] MySQL: schema ready on {DB_HOST}:{DB_PORT}/{DB_NAME}")
        finally:
            conn.close()


def migrate_database_schema(cur):
    cur.execute(
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=%s AND TABLE_NAME='accounts' "
        "AND COLUMN_NAME='email'",
        (DB_NAME,))
    row = cur.fetchone()
    if not row or int(row[0]) == 0:
        cur.execute(
            "ALTER TABLE accounts ADD COLUMN email "
            "VARCHAR(255) NULL DEFAULT NULL AFTER username")
        cur.execute(
            "ALTER TABLE accounts ADD UNIQUE KEY uniq_email (email)")
    cur.execute(
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=%s AND TABLE_NAME='tankmen' "
        "AND COLUMN_NAME='slot_idx'",
        (DB_NAME,))
    row = cur.fetchone()
    if not row or int(row[0]) == 0:
        cur.execute(
            "ALTER TABLE tankmen ADD COLUMN slot_idx "
            "TINYINT UNSIGNED NULL AFTER vehicle_inv_id")
    cur.execute(
        "SELECT inv_id, account_id, vehicle_inv_id FROM tankmen "
        "WHERE vehicle_inv_id IS NOT NULL AND slot_idx IS NULL "
        "ORDER BY account_id, vehicle_inv_id, inv_id")
    rows = cur.fetchall() or []
    counters = {}
    for inv_id, acc_id, veh_inv_id in rows:
        key = (int(acc_id), int(veh_inv_id))
        slot_idx = counters.get(key, 0)
        counters[key] = slot_idx + 1
        cur.execute(
            "UPDATE tankmen SET slot_idx=%s WHERE inv_id=%s",
            (slot_idx, int(inv_id)))
    cur.execute(
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=%s AND TABLE_NAME='vehicle_ammo_layouts' "
        "AND COLUMN_NAME='slot_idx'",
        (DB_NAME,))
    row = cur.fetchone()
    ammo_slot_idx_added = False
    if not row or int(row[0]) == 0:
        cur.execute(
            "ALTER TABLE vehicle_ammo_layouts ADD COLUMN slot_idx "
            "TINYINT UNSIGNED NOT NULL DEFAULT 0 AFTER vehicle_inv_id")
        ammo_slot_idx_added = True
    if ammo_slot_idx_added:
        cur.execute(
            "SELECT account_id, vehicle_inv_id, shell_compact_descr "
            "FROM vehicle_ammo_layouts "
            "ORDER BY account_id, vehicle_inv_id, shell_compact_descr")
        rows = cur.fetchall() or []
        counters = {}
        for acc_id, veh_inv_id, shell_cd in rows:
            key = (int(acc_id), int(veh_inv_id))
            slot_idx = counters.get(key, 0)
            counters[key] = slot_idx + 1
            cur.execute(
                "UPDATE vehicle_ammo_layouts SET slot_idx=%s "
                "WHERE account_id=%s AND vehicle_inv_id=%s "
                "AND shell_compact_descr=%s",
                (slot_idx, int(acc_id), int(veh_inv_id), int(shell_cd)))
    cur.execute(
        "SELECT COLUMN_NAME FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA=%s AND TABLE_NAME='vehicle_ammo_layouts' "
        "AND INDEX_NAME='PRIMARY' ORDER BY SEQ_IN_INDEX",
        (DB_NAME,))
    pk_cols = [str(r[0]) for r in (cur.fetchall() or [])]
    if pk_cols != ['account_id', 'vehicle_inv_id', 'slot_idx']:
        cur.execute(
            "SELECT account_id, vehicle_inv_id, shell_compact_descr "
            "FROM vehicle_ammo_layouts "
            "ORDER BY account_id, vehicle_inv_id, slot_idx, "
            "shell_compact_descr")
        rows = cur.fetchall() or []
        counters = {}
        for acc_id, veh_inv_id, shell_cd in rows:
            key = (int(acc_id), int(veh_inv_id))
            slot_idx = counters.get(key, 0)
            counters[key] = slot_idx + 1
            cur.execute(
                "UPDATE vehicle_ammo_layouts SET slot_idx=%s "
                "WHERE account_id=%s AND vehicle_inv_id=%s "
                "AND shell_compact_descr=%s",
                (slot_idx, int(acc_id), int(veh_inv_id), int(shell_cd)))
        cur.execute(
            "ALTER TABLE vehicle_ammo_layouts DROP PRIMARY KEY, "
            "ADD PRIMARY KEY(account_id, vehicle_inv_id, slot_idx)")


def get_or_create_account(username: str, password: str):
    email_input = (username or '').strip().lower()
    pwd_hash = password_hash(password)
    now = datetime.datetime.now()
    existing_account_id = None
    existing_username = None
    with db_lock:
        conn = db_connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, username, password_hash FROM accounts "
                    "WHERE email=%s",
                    (email_input,))
                row = cur.fetchone()
                if row is None:
                    norm_name = normalize_login_name(username).lower()
                    cur.execute(
                        "SELECT id, username, password_hash FROM accounts "
                        "WHERE normalized_name=%s",
                        (norm_name,))
                    row = cur.fetchone()
                if row is None:
                    return {'id': 0, 'username': username,
                            'created': False, 'auth_failed': True}
                else:
                    if str(row[2]) != pwd_hash:
                        return {'id': 0, 'username': row[1],
                                'created': False, 'auth_failed': True}
                    cur.execute(
                        "UPDATE accounts SET last_login=%s WHERE id=%s",
                        (now, row[0]))
                    existing_account_id = int(row[0])
                    existing_username = row[1]
        finally:
            conn.close()
    if existing_account_id is not None:
        try:
            print(f"[*] re-seeding existing account id={existing_account_id}")
            seed_default_account_inventory(existing_account_id)
            invalidate_sync_cache(existing_account_id)
        except Exception as e:
            print(f"[!] auto-seed for existing account failed: {e}")
            import traceback
            traceback.print_exc()
        return {'id': existing_account_id, 'username': existing_username,
                'created': False}
    return {'id': 0, 'username': username, 'created': False,
            'auth_failed': True}


def count_account_tankmen(account_id: int) -> int:
    with db_lock:
        conn = db_connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM tankmen WHERE account_id=%s",
                    (int(account_id),))
                row = cur.fetchone()
        finally:
            conn.close()
    return int(row[0]) if row else 0


def seed_default_account_inventory(account_id: int):
    account_id = int(account_id)
    veh_list = load_all_vehicles()
    if not veh_list:
        return
    now = datetime.datetime.now()

    DEFAULT_CONSUMABLES = (
        (ITEM_TYPE_EQUIPMENT, 4, 50),
        (ITEM_TYPE_EQUIPMENT, 2, 50),
        (ITEM_TYPE_EQUIPMENT, 1, 50),
    )
    DEFAULT_OPT_DEVICES = (
        (ITEM_TYPE_OPTIONAL_DEVICE, 4, 50),
        (ITEM_TYPE_OPTIONAL_DEVICE, 5, 50),
        (ITEM_TYPE_OPTIONAL_DEVICE, 1, 50),
    )

    with db_lock:
        conn = db_connect()
        try:
            with conn.cursor() as cur:
                for item_type, in_nation, qty in DEFAULT_CONSUMABLES:
                    cd = make_artefact_compact_descr(item_type, in_nation)
                    cur.execute(
                        "INSERT INTO account_consumables(account_id, "
                        "compact_descr, quantity) VALUES (%s, %s, %s) "
                        "ON DUPLICATE KEY UPDATE quantity=quantity",
                        (account_id, cd, qty))
                for item_type, in_nation, qty in DEFAULT_OPT_DEVICES:
                    cd = make_artefact_compact_descr(item_type, in_nation)
                    cur.execute(
                        "INSERT INTO account_optional_devices(account_id, "
                        "compact_descr, quantity) VALUES (%s, %s, %s) "
                        "ON DUPLICATE KEY UPDATE quantity=quantity",
                        (account_id, cd, qty))
                cur.execute(
                    "SELECT vehicle_inv_id, COUNT(*) FROM tankmen "
                    "WHERE account_id=%s AND vehicle_inv_id IS NOT NULL "
                    "GROUP BY vehicle_inv_id",
                    (account_id,))
                tankmen_existing = {
                    int(veh_inv_id): int(count)
                    for veh_inv_id, count in (cur.fetchall() or [])
                    if veh_inv_id is not None
                }
                cur.execute(
                    "SELECT vehicle_inv_id, COUNT(*) "
                    "FROM vehicle_consumable_slots WHERE account_id=%s "
                    "GROUP BY vehicle_inv_id",
                    (account_id,))
                consumable_existing = {
                    int(veh_inv_id): int(count)
                    for veh_inv_id, count in (cur.fetchall() or [])
                    if veh_inv_id is not None
                }
                cur.execute(
                    "SELECT vehicle_inv_id, COUNT(*) "
                    "FROM vehicle_optional_device_slots WHERE account_id=%s "
                    "GROUP BY vehicle_inv_id",
                    (account_id,))
                opt_existing = {
                    int(veh_inv_id): int(count)
                    for veh_inv_id, count in (cur.fetchall() or [])
                    if veh_inv_id is not None
                }

                consumable_layout = [
                    make_artefact_compact_descr(ITEM_TYPE_EQUIPMENT, 4),
                    make_artefact_compact_descr(ITEM_TYPE_EQUIPMENT, 2),
                    make_artefact_compact_descr(ITEM_TYPE_EQUIPMENT, 1),
                ]
                opt_device_layout = [
                    make_artefact_compact_descr(ITEM_TYPE_OPTIONAL_DEVICE, 4),
                    make_artefact_compact_descr(ITEM_TYPE_OPTIONAL_DEVICE, 5),
                    make_artefact_compact_descr(ITEM_TYPE_OPTIONAL_DEVICE, 1),
                ]
                tankmen_rows = []
                consumable_rows = []
                opt_rows = []
                for vehicle in veh_list:
                    veh_inv_id = int(vehicle['inv_id'])
                    nation_id = int(vehicle.get('nationID') or 0)
                    vehicle_type_id = int(vehicle.get('vehicleTypeID') or 0)
                    nation_name = vehicle.get('nation') or 'ussr'
                    crew_size = int(vehicle.get('crewSize') or 4)
                    if int(tankmen_existing.get(veh_inv_id, 0)) <= 0:
                        for slot in range(min(crew_size, len(DEFAULT_CREW_ROLES))):
                            passport = pick_random_passport(nation_name)
                            if passport is None:
                                continue
                            tankmen_rows.append((
                                account_id, veh_inv_id, slot, nation_id,
                                vehicle_type_id, DEFAULT_CREW_ROLES[slot],
                                1 if passport['is_female'] else 0,
                                passport['first_name_id'],
                                passport['last_name_id'],
                                passport['icon_id'], now))
                    if int(consumable_existing.get(veh_inv_id, 0)) <= 0:
                        for slot_idx, cd in enumerate(consumable_layout):
                            consumable_rows.append((
                                account_id, veh_inv_id, slot_idx, cd))
                    if int(opt_existing.get(veh_inv_id, 0)) <= 0:
                        for slot_idx, cd in enumerate(opt_device_layout):
                            opt_rows.append((
                                account_id, veh_inv_id, slot_idx, cd))
                if tankmen_rows:
                    cur.executemany(
                        "INSERT INTO tankmen(account_id, vehicle_inv_id, "
                        "slot_idx, nation_id, vehicle_type_id, role_id, "
                        "role_level, is_female, is_premium, first_name_id, "
                        "last_name_id, icon_id, free_xp, skills, "
                        "last_skill_level, created_at) VALUES "
                        "(%s, %s, %s, %s, %s, %s, 100, %s, 0, %s, %s, %s, "
                        "0, '', 0, %s)",
                        tankmen_rows)
                if consumable_rows:
                    cur.executemany(
                        "INSERT INTO vehicle_consumable_slots(account_id, "
                        "vehicle_inv_id, slot_idx, compact_descr) "
                        "VALUES (%s, %s, %s, %s) "
                        "ON DUPLICATE KEY UPDATE "
                        "compact_descr=VALUES(compact_descr)",
                        consumable_rows)
                if opt_rows:
                    cur.executemany(
                        "INSERT INTO vehicle_optional_device_slots(account_id, "
                        "vehicle_inv_id, slot_idx, compact_descr) "
                        "VALUES (%s, %s, %s, %s) "
                        "ON DUPLICATE KEY UPDATE "
                        "compact_descr=VALUES(compact_descr)",
                        opt_rows)
                if tankmen_rows or consumable_rows or opt_rows:
                    print(f"[*] seed_default_account_inventory: "
                          f"account={account_id} "
                          f"tankmen={len(tankmen_rows)} "
                          f"eqSlots={len(consumable_rows)} "
                          f"optSlots={len(opt_rows)}")
                fix_legacy_tankman_nations(cur, account_id, veh_list)
        finally:
            conn.close()


def fix_legacy_tankman_nations(cur, account_id: int, veh_list):
    veh_by_inv = get_vehicle_inventory_map()
    valid_pairs = {(int(v.get('nationID') or 0),
                    int(v.get('vehicleTypeID') or 0)) for v in veh_list}
    nation_default_vtype = {}
    for v in veh_list:
        n = int(v.get('nationID') or 0)
        t = int(v.get('vehicleTypeID') or 0)
        nation_default_vtype.setdefault(n, t)

    cur.execute(
        "SELECT inv_id, vehicle_inv_id, nation_id, vehicle_type_id, "
        "first_name_id, last_name_id, icon_id, is_female, is_premium "
        "FROM tankmen WHERE account_id=%s",
        (int(account_id),))
    rows = cur.fetchall() or []
    fixed = 0
    for row in rows:
        tm_inv = int(row[0])
        veh_inv = int(row[1]) if row[1] is not None else 0
        nation_id = int(row[2])
        vehicle_type_id = int(row[3])
        is_premium = bool(row[8])

        if veh_inv:
            vehicle = veh_by_inv.get(veh_inv)
            if vehicle is None:
                continue
            target_nation = int(vehicle.get('nationID') or 0)
            target_vtype  = int(vehicle.get('vehicleTypeID') or 0)
            target_nation_name = vehicle.get('nation') or 'ussr'
        else:
            if (nation_id, vehicle_type_id) in valid_pairs:
                continue
            target_nation = nation_id
            target_vtype = nation_default_vtype.get(nation_id)
            if target_vtype is None or (target_nation, target_vtype) not in valid_pairs:
                target_nation = 0
                target_vtype = nation_default_vtype.get(0, 0)
            target_nation_name = NATION_NAMES[target_nation] \
                if 0 <= target_nation < len(NATION_NAMES) else 'ussr'

        if nation_id == target_nation and vehicle_type_id == target_vtype:
            continue
        passport = pick_random_passport(target_nation_name,
                                        is_premium=is_premium)
        if passport is None:
            cur.execute(
                "UPDATE tankmen SET nation_id=%s, vehicle_type_id=%s "
                "WHERE inv_id=%s AND account_id=%s",
                (target_nation, target_vtype, tm_inv, int(account_id)))
            fixed += 1
            continue
        cur.execute(
            "UPDATE tankmen SET nation_id=%s, vehicle_type_id=%s, "
            "is_female=%s, first_name_id=%s, last_name_id=%s, icon_id=%s "
            "WHERE inv_id=%s AND account_id=%s",
            (target_nation, target_vtype,
             1 if passport['is_female'] else 0,
             passport['first_name_id'], passport['last_name_id'],
             passport['icon_id'], tm_inv, int(account_id)))
        fixed += 1
    print(f"[*] fix_legacy_tankman_nations: account={account_id} "
          f"fixed={fixed}/{len(rows)} tankmen")


def seed_vehicle_default_inventory(cur, account_id, vehicle, now):
    veh_inv_id      = int(vehicle['inv_id'])
    nation_id       = int(vehicle.get('nationID') or 0)
    vehicle_type_id = int(vehicle.get('vehicleTypeID') or 0)
    nation_name     = vehicle.get('nation') or 'ussr'
    crew_size       = int(vehicle.get('crewSize') or 4)

    cur.execute(
        "SELECT COUNT(*) FROM tankmen WHERE account_id=%s "
        "AND vehicle_inv_id=%s",
        (int(account_id), veh_inv_id))
    row = cur.fetchone()
    if not (row and int(row[0]) > 0):
        for slot in range(min(crew_size, len(DEFAULT_CREW_ROLES))):
            role_id = DEFAULT_CREW_ROLES[slot]
            passport = pick_random_passport(nation_name)
            if passport is None:
                continue
            cur.execute(
                "INSERT INTO tankmen(account_id, vehicle_inv_id, "
                "slot_idx, nation_id, vehicle_type_id, role_id, "
                "role_level, is_female, is_premium, first_name_id, "
                "last_name_id, icon_id, free_xp, skills, "
                "last_skill_level, created_at) VALUES "
                "(%s, %s, %s, %s, %s, %s, 100, %s, 0, %s, %s, %s, "
                "0, '', 0, %s)",
                (int(account_id), veh_inv_id, slot, nation_id,
                 vehicle_type_id, role_id,
                 1 if passport['is_female'] else 0,
                 passport['first_name_id'],
                 passport['last_name_id'], passport['icon_id'], now))

    cur.execute(
        "SELECT COUNT(*) FROM vehicle_consumable_slots "
        "WHERE account_id=%s AND vehicle_inv_id=%s",
        (int(account_id), veh_inv_id))
    row = cur.fetchone()
    if not (row and int(row[0]) > 0):
        consumable_layout = [
            make_artefact_compact_descr(ITEM_TYPE_EQUIPMENT, 4),
            make_artefact_compact_descr(ITEM_TYPE_EQUIPMENT, 2),
            make_artefact_compact_descr(ITEM_TYPE_EQUIPMENT, 1),
        ]
        for slot_idx, cd in enumerate(consumable_layout):
            cur.execute(
                "INSERT INTO vehicle_consumable_slots(account_id, "
                "vehicle_inv_id, slot_idx, compact_descr) "
                "VALUES (%s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE "
                "compact_descr=VALUES(compact_descr)",
                (int(account_id), veh_inv_id, slot_idx, cd))

    cur.execute(
        "SELECT COUNT(*) FROM vehicle_optional_device_slots "
        "WHERE account_id=%s AND vehicle_inv_id=%s",
        (int(account_id), veh_inv_id))
    row = cur.fetchone()
    if not (row and int(row[0]) > 0):
        opt_device_layout = [
            make_artefact_compact_descr(ITEM_TYPE_OPTIONAL_DEVICE, 4),
            make_artefact_compact_descr(ITEM_TYPE_OPTIONAL_DEVICE, 5),
            make_artefact_compact_descr(ITEM_TYPE_OPTIONAL_DEVICE, 1),
        ]
        for slot_idx, cd in enumerate(opt_device_layout):
            cur.execute(
                "INSERT INTO vehicle_optional_device_slots(account_id, "
                "vehicle_inv_id, slot_idx, compact_descr) "
                "VALUES (%s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE "
                "compact_descr=VALUES(compact_descr)",
                (int(account_id), veh_inv_id, slot_idx, cd))

def store_battle_entry(arena_type_id: int, account_id: int, vehicle_inv_id: int,
                       team: int = 0, queue_type: int = 0):
    now = datetime.datetime.now()
    with db_lock:
        conn = db_connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM battles WHERE finished_at IS NULL "
                    "ORDER BY id DESC LIMIT 1")
                row = cur.fetchone()
                if row is None:
                    cur.execute(
                        "INSERT INTO battles(arena_type_id, queue_type, "
                        "created_at) VALUES (%s, %s, %s)",
                        (int(arena_type_id), int(queue_type), now))
                    battle_id = int(cur.lastrowid)
                else:
                    battle_id = int(row[0])
                cur.execute(
                    "INSERT INTO battle_entries(battle_id, account_id, "
                    "vehicle_inv_id, team, joined_at) VALUES "
                    "(%s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE "
                    "vehicle_inv_id=VALUES(vehicle_inv_id), "
                    "team=VALUES(team), joined_at=VALUES(joined_at)",
                    (battle_id, int(account_id), int(vehicle_inv_id),
                     int(team), now))
                return battle_id
        finally:
            conn.close()


def load_account_state(account_id: int) -> dict:
    with db_lock:
        conn = db_connect()
        try:
            with conn.cursor(pymysql.cursors.DictCursor) as cur:
                cur.execute(
                    "SELECT credits, gold, free_xp, slots, berths, "
                    "premium_expire_at, attrs, clan_db_id "
                    "FROM accounts WHERE id=%s", (int(account_id),))
                row = cur.fetchone() or {}
                cur.execute(
                    "SELECT item_compact_descr FROM account_unlocks "
                    "WHERE account_id=%s", (int(account_id),))
                unlocks = {int(r['item_compact_descr']) for r in cur.fetchall()}
                cur.execute(
                    "SELECT vehicle_compact_descr FROM account_elite_vehicles "
                    "WHERE account_id=%s", (int(account_id),))
                elites = {int(r['vehicle_compact_descr']) for r in cur.fetchall()}
                cur.execute(
                    "SELECT vehicle_compact_descr FROM account_double_xp_vehicles "
                    "WHERE account_id=%s", (int(account_id),))
                dblxp = {int(r['vehicle_compact_descr']) for r in cur.fetchall()}
        finally:
            conn.close()
    return {
        'credits':         int(row.get('credits', DEFAULT_CREDITS) or 0),
        'gold':            int(row.get('gold', DEFAULT_GOLD) or 0),
        'free_xp':         int(row.get('free_xp', DEFAULT_FREE_XP) or 0),
        'slots':           int(row.get('slots', DEFAULT_SLOTS) or 0),
        'berths':          int(row.get('berths', DEFAULT_BERTHS) or 0),
        'premium_expire':  int(row.get('premium_expire_at', 0) or 0),
        'attrs':           int(row.get('attrs', 0) or 0),
        'clan_db_id':      int(row.get('clan_db_id', 0) or 0),
        'unlocks':         unlocks,
        'elite_vehicles':  elites,
        'double_xp_vehs':  dblxp,
    }


def load_account_tankmen(account_id: int) -> list:
    with db_lock:
        conn = db_connect()
        try:
            with conn.cursor(pymysql.cursors.DictCursor) as cur:
                cur.execute(
                    "SELECT inv_id, vehicle_inv_id, slot_idx, nation_id, "
                    "vehicle_type_id, role_id, role_level, is_female, "
                    "is_premium, "
                    "first_name_id, last_name_id, icon_id, free_xp, "
                    "skills, last_skill_level "
                    "FROM tankmen WHERE account_id=%s ORDER BY inv_id",
                    (int(account_id),))
                rows = cur.fetchall() or []
        finally:
            conn.close()
    out = []
    for r in rows:
        skills_str = (r.get('skills') or '').strip()
        skills = []
        if skills_str:
            try:
                skills = [int(x) for x in skills_str.split(',') if x.strip()]
            except Exception:
                skills = []
        out.append({
            'inv_id':         int(r['inv_id']),
            'vehicle_inv_id': (int(r['vehicle_inv_id'])
                               if r.get('vehicle_inv_id') is not None else 0),
            'slot_idx':       (int(r['slot_idx'])
                               if r.get('slot_idx') is not None else None),
            'nation_id':      int(r['nation_id']),
            'vehicle_type_id':int(r['vehicle_type_id']),
            'role_id':        int(r['role_id']),
            'role_level':     int(r['role_level']),
            'is_female':      bool(r['is_female']),
            'is_premium':     bool(r['is_premium']),
            'first_name_id':  int(r['first_name_id']),
            'last_name_id':   int(r['last_name_id']),
            'icon_id':        int(r['icon_id']),
            'free_xp':        int(r['free_xp']),
            'skills':         skills,
            'last_skill_level': int(r['last_skill_level']),
        })
    return out


def load_account_consumables(account_id: int) -> dict:
    with db_lock:
        conn = db_connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT compact_descr, quantity FROM account_consumables "
                    "WHERE account_id=%s AND quantity > 0",
                    (int(account_id),))
                rows = cur.fetchall() or []
        finally:
            conn.close()
    return {int(cd): int(qty) for cd, qty in rows}


def load_account_optional_devices(account_id: int) -> dict:
    with db_lock:
        conn = db_connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT compact_descr, quantity FROM account_optional_devices "
                    "WHERE account_id=%s AND quantity > 0",
                    (int(account_id),))
                rows = cur.fetchall() or []
        finally:
            conn.close()
    return {int(cd): int(qty) for cd, qty in rows}


def load_vehicle_consumable_slots(account_id: int) -> dict:
    with db_lock:
        conn = db_connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT vehicle_inv_id, slot_idx, compact_descr "
                    "FROM vehicle_consumable_slots WHERE account_id=%s",
                    (int(account_id),))
                rows = cur.fetchall() or []
        finally:
            conn.close()
    out = {}
    for veh_inv_id, slot_idx, cd in rows:
        slots = out.setdefault(int(veh_inv_id), [0, 0, 0])
        if 0 <= slot_idx < len(slots):
            slots[int(slot_idx)] = int(cd)
    return out


DEFAULT_ARTEFACT_PRICE_CREDITS = 5000
DEFAULT_OPT_DEVICE_PRICE_CREDITS = 100000


def get_artefact_price(item_type: int, in_nation_id: int) -> tuple:
    cfg = load_artefacts_config() or {}
    section = 'equipments' if item_type == ITEM_TYPE_EQUIPMENT \
              else 'optional_devices' if item_type == ITEM_TYPE_OPTIONAL_DEVICE \
              else None
    if section:
        for it in cfg.get(section, []):
            if int(it.get('id') or 0) == int(in_nation_id):
                price = it.get('price') or [0, 0]
                credits = max(0, int(price[0] if len(price) > 0 else 0))
                gold    = max(0, int(price[1] if len(price) > 1 else 0))
                if credits == 0 and gold == 0:
                    if item_type == ITEM_TYPE_EQUIPMENT:
                        credits = DEFAULT_ARTEFACT_PRICE_CREDITS
                    elif item_type == ITEM_TYPE_OPTIONAL_DEVICE:
                        credits = DEFAULT_OPT_DEVICE_PRICE_CREDITS
                return (credits, gold)
    if item_type == ITEM_TYPE_EQUIPMENT:
        return (DEFAULT_ARTEFACT_PRICE_CREDITS, 0)
    if item_type == ITEM_TYPE_OPTIONAL_DEVICE:
        return (DEFAULT_OPT_DEVICE_PRICE_CREDITS, 0)
    return (0, 0)


def buy_item_for_account(account_id: int, item_compact_descr: int,
                         count: int) -> dict:
    if not account_id or count <= 0:
        return {'success': False, 'reason': 'invalid args'}
    item_type   = item_compact_descr & 0x0f
    nation_id   = (item_compact_descr >> 4) & 0x0f
    in_nation_id = (item_compact_descr >> 8) & 0xffff
    if item_type not in (ITEM_TYPE_EQUIPMENT, ITEM_TYPE_OPTIONAL_DEVICE):
        return {'success': False,
                'reason': f'item_type {item_type} not buyable'}
    credits_each, gold_each = get_artefact_price(item_type, in_nation_id)
    total_credits = credits_each * int(count)
    total_gold    = gold_each    * int(count)
    table = ('account_consumables' if item_type == ITEM_TYPE_EQUIPMENT
             else 'account_optional_devices')
    with db_lock:
        conn = db_connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT credits, gold FROM accounts WHERE id=%s",
                    (int(account_id),))
                row = cur.fetchone()
                if row is None:
                    return {'success': False, 'reason': 'no account'}
                have_credits, have_gold = int(row[0]), int(row[1])
                if have_credits < total_credits or have_gold < total_gold:
                    return {'success': False,
                            'reason': f'insufficient funds '
                            f'(need {total_credits}c {total_gold}g, '
                            f'have {have_credits}c {have_gold}g)'}
                cur.execute(
                    "UPDATE accounts SET credits=credits-%s, gold=gold-%s "
                    "WHERE id=%s",
                    (total_credits, total_gold, int(account_id)))
                cur.execute(
                    f"INSERT INTO {table}(account_id, compact_descr, "
                    "quantity) VALUES (%s, %s, %s) ON DUPLICATE KEY UPDATE "
                    "quantity=quantity+VALUES(quantity)",
                    (int(account_id), int(item_compact_descr), int(count)))
        finally:
            conn.close()
    return {'success': True, 'price': (total_credits, total_gold)}


def load_vehicle_optional_device_slots(account_id: int) -> dict:
    with db_lock:
        conn = db_connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT vehicle_inv_id, slot_idx, compact_descr "
                    "FROM vehicle_optional_device_slots WHERE account_id=%s",
                    (int(account_id),))
                rows = cur.fetchall() or []
        finally:
            conn.close()
    out = {}
    for veh_inv_id, slot_idx, cd in rows:
        slots = out.setdefault(int(veh_inv_id), [0, 0, 0])
        if 0 <= slot_idx < len(slots):
            slots[int(slot_idx)] = int(cd)
    return out


def load_vehicle_ammo_layouts(account_id: int) -> dict:
    with db_lock:
        conn = db_connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT vehicle_inv_id, slot_idx, "
                    "shell_compact_descr, quantity "
                    "FROM vehicle_ammo_layouts WHERE account_id=%s "
                    "ORDER BY vehicle_inv_id, slot_idx",
                    (int(account_id),))
                rows = cur.fetchall() or []
        finally:
            conn.close()
    out = {}
    for veh_inv_id, slot_idx, cd, qty in rows:
        out.setdefault(int(veh_inv_id), []).append(
            (int(slot_idx), int(cd), int(qty)))
    flat = {}
    for veh_inv_id, items in out.items():
        items.sort(key=lambda x: x[0])
        flat[veh_inv_id] = []
        for _, cd, qty in items:
            flat[veh_inv_id].extend([cd, qty])
    return flat


def save_consumable_slots(account_id: int, vehicle_inv_id: int,
                          slot_cds: list):
    with db_lock:
        conn = db_connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM vehicle_consumable_slots "
                    "WHERE account_id=%s AND vehicle_inv_id=%s",
                    (int(account_id), int(vehicle_inv_id)))
                for slot_idx, cd in enumerate(slot_cds):
                    cd = int(cd)
                    if cd <= 0:
                        cd = 0
                    cur.execute(
                        "INSERT INTO vehicle_consumable_slots(account_id, "
                        "vehicle_inv_id, slot_idx, compact_descr) "
                        "VALUES (%s, %s, %s, %s)",
                        (int(account_id), int(vehicle_inv_id),
                         int(slot_idx), cd))
        finally:
            conn.close()


def save_optional_device_slot(account_id: int, vehicle_inv_id: int,
                              slot_idx: int, compact_descr: int):
    with db_lock:
        conn = db_connect()
        try:
            with conn.cursor() as cur:
                compact_descr = int(compact_descr)
                if compact_descr > 0:
                    cur.execute(
                        "INSERT INTO vehicle_optional_device_slots("
                        "account_id, vehicle_inv_id, slot_idx, "
                        "compact_descr) VALUES (%s, %s, %s, %s) "
                        "ON DUPLICATE KEY UPDATE "
                        "compact_descr=VALUES(compact_descr)",
                        (int(account_id), int(vehicle_inv_id),
                         int(slot_idx), compact_descr))
                else:
                    cur.execute(
                        "DELETE FROM vehicle_optional_device_slots "
                        "WHERE account_id=%s AND vehicle_inv_id=%s "
                        "AND slot_idx=%s",
                        (int(account_id), int(vehicle_inv_id),
                         int(slot_idx)))
        finally:
            conn.close()


def save_vehicle_ammo_layout(account_id: int, vehicle_inv_id: int,
                             ammo_pairs: list):
    with db_lock:
        conn = db_connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM vehicle_ammo_layouts "
                    "WHERE account_id=%s AND vehicle_inv_id=%s",
                    (int(account_id), int(vehicle_inv_id)))
                slot_idx = 0
                i = 0
                while i + 1 < len(ammo_pairs) and slot_idx < 8:
                    cd  = int(ammo_pairs[i])
                    qty = max(0, int(ammo_pairs[i + 1]))
                    if cd > 0:
                        cur.execute(
                            "INSERT INTO vehicle_ammo_layouts("
                            "account_id, vehicle_inv_id, slot_idx, "
                            "shell_compact_descr, quantity) "
                            "VALUES (%s, %s, %s, %s, %s)",
                            (int(account_id), int(vehicle_inv_id),
                             slot_idx, cd, qty))
                        slot_idx += 1
                    i += 2
        finally:
            conn.close()


def set_tankman_vehicle(account_id: int, tankman_inv_id: int,
                        vehicle_inv_id, slot_idx=None):
    veh = (int(vehicle_inv_id) if vehicle_inv_id is not None
           and int(vehicle_inv_id) > 0 else None)
    slot = (int(slot_idx) if veh is not None and slot_idx is not None
            and int(slot_idx) >= 0 else None)
    prev_veh = 0
    kicked_veh = 0
    with db_lock:
        conn = db_connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT vehicle_inv_id FROM tankmen "
                    "WHERE inv_id=%s AND account_id=%s",
                    (int(tankman_inv_id), int(account_id)))
                row = cur.fetchone()
                prev_veh = int(row[0]) if row and row[0] is not None else 0
                if veh is not None and slot is not None:
                    cur.execute(
                        "UPDATE tankmen SET vehicle_inv_id=NULL, "
                        "slot_idx=NULL WHERE account_id=%s "
                        "AND vehicle_inv_id=%s AND slot_idx=%s "
                        "AND inv_id<>%s",
                        (int(account_id), veh, slot, int(tankman_inv_id)))
                    kicked_veh = veh if int(cur.rowcount or 0) > 0 else 0
                cur.execute(
                    "UPDATE tankmen SET vehicle_inv_id=%s, slot_idx=%s "
                    "WHERE inv_id=%s AND account_id=%s",
                    (veh, slot, int(tankman_inv_id), int(account_id)))
                affected = cur.rowcount
        finally:
            conn.close()
    return (int(affected or 0) > 0, prev_veh, kicked_veh)


def clear_tankman_vehicle_slot(account_id: int, vehicle_inv_id: int,
                               slot_idx: int) -> bool:
    with db_lock:
        conn = db_connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE tankmen SET vehicle_inv_id=NULL, slot_idx=NULL "
                    "WHERE account_id=%s AND vehicle_inv_id=%s "
                    "AND slot_idx=%s",
                    (int(account_id), int(vehicle_inv_id), int(slot_idx)))
                affected = cur.rowcount
        finally:
            conn.close()
    return int(affected or 0) > 0


def dismiss_tankman(account_id: int, tankman_inv_id: int):
    with db_lock:
        conn = db_connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT vehicle_inv_id FROM tankmen "
                    "WHERE inv_id=%s AND account_id=%s",
                    (int(tankman_inv_id), int(account_id)))
                row = cur.fetchone()
                veh_inv_id = int(row[0]) if row and row[0] is not None else 0
                cur.execute(
                    "DELETE FROM tankmen "
                    "WHERE inv_id=%s AND account_id=%s",
                    (int(tankman_inv_id), int(account_id)))
                affected = cur.rowcount
        finally:
            conn.close()
    return (int(affected or 0) > 0, veh_inv_id)


def record_battle_result(battle_id: int, account_id: int,
                         vehicle_inv_id: int, results: dict):
    now = datetime.datetime.now()
    is_winner = 1 if results.get('isWinner') else 0
    frags = int(len(results.get('killed') or []))
    damage_dealt = int(results.get('damageDealt', 0))
    damage_received = int(results.get('damageReceived', 0))
    shots = int(results.get('shots', 0))
    hits = int(results.get('hits', 0))
    life_time = int(results.get('lifeTime', 0))
    credits_earned = int(results.get('credits', 0))
    xp_earned = int(results.get('xp', 0))
    free_xp_earned = int(results.get('freeXP', 0))
    with db_lock:
        conn = db_connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO battle_results(battle_id, account_id, "
                    "vehicle_inv_id, is_winner, frags, damage_dealt, "
                    "damage_received, shots, hits, life_time_sec, "
                    "credits_earned, xp_earned, free_xp_earned, finished_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                    "%s, %s, %s) ON DUPLICATE KEY UPDATE "
                    "is_winner=VALUES(is_winner), frags=VALUES(frags), "
                    "damage_dealt=VALUES(damage_dealt), "
                    "damage_received=VALUES(damage_received), "
                    "shots=VALUES(shots), hits=VALUES(hits), "
                    "life_time_sec=VALUES(life_time_sec), "
                    "credits_earned=VALUES(credits_earned), "
                    "xp_earned=VALUES(xp_earned), "
                    "free_xp_earned=VALUES(free_xp_earned), "
                    "finished_at=VALUES(finished_at)",
                    (int(battle_id), int(account_id), int(vehicle_inv_id),
                     is_winner, frags, damage_dealt, damage_received,
                     shots, hits, life_time, credits_earned, xp_earned,
                     free_xp_earned, now))
                cur.execute(
                    "UPDATE accounts SET credits=credits+%s, free_xp=free_xp+%s "
                    "WHERE id=%s",
                    (credits_earned, free_xp_earned, int(account_id)))
                wins_inc   = 1 if is_winner else 0
                losses_inc = 0 if is_winner else 1
                cur.execute(
                    "INSERT INTO dossier(account_id, total_battles, wins, "
                    "losses, frags, damage_dealt, damage_received, shots, "
                    "hits, max_xp, max_damage, max_frags, total_xp, "
                    "total_credits, last_battle_at) VALUES "
                    "(%s, 1, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "ON DUPLICATE KEY UPDATE "
                    "total_battles=total_battles+1, wins=wins+VALUES(wins), "
                    "losses=losses+VALUES(losses), "
                    "frags=frags+VALUES(frags), "
                    "damage_dealt=damage_dealt+VALUES(damage_dealt), "
                    "damage_received=damage_received+VALUES(damage_received), "
                    "shots=shots+VALUES(shots), hits=hits+VALUES(hits), "
                    "max_xp=GREATEST(max_xp, VALUES(max_xp)), "
                    "max_damage=GREATEST(max_damage, VALUES(max_damage)), "
                    "max_frags=GREATEST(max_frags, VALUES(max_frags)), "
                    "total_xp=total_xp+VALUES(total_xp), "
                    "total_credits=total_credits+VALUES(total_credits), "
                    "last_battle_at=VALUES(last_battle_at)",
                    (int(account_id), wins_inc, losses_inc, frags,
                     damage_dealt, damage_received, shots, hits,
                     xp_earned, damage_dealt, frags, xp_earned,
                     credits_earned, now))
        finally:
            conn.close()


def mark_battle_finished(battle_id, winner_team, finish_reason):
    if not battle_id:
        return
    now = datetime.datetime.now()
    with db_lock:
        conn = db_connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE battles SET finished_at=%s, winner_team=%s, "
                    "finish_reason=%s WHERE id=%s AND finished_at IS NULL",
                    (now, int(winner_team) if winner_team is not None else None,
                     int(finish_reason) if finish_reason is not None else None,
                     int(battle_id)))
        finally:
            conn.close()

def get_online_count() -> int:
    seen = set()
    for sess in baseapp_clients.values():
        account_id = sess.get('account_id')
        if account_id:
            seen.add(account_id)
    return len(seen)

def get_active_battle_count() -> int:
    with battle_lock:
        return 1 if active_battle_accounts else 0

def get_server_stats():
    online = get_online_count()
    battles = get_active_battle_count()
    return {
        'playersCount': online,
        'arenasCount': battles,
    }

def find_rsa_block(data):
    for start in range(15, 30):
        if start + 256 > len(data): continue
        dec = rsa_decrypt_oaep(data[start:start+256], private_key)
        if dec and dec[0] in (0x00, 0x01):
            return dec
    return None

# в”Ђв”Ђ BigWorld Blowfish (XOR-chain ECB) в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

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

    seq = int(seq) & PACKET_SEQ_MASK
    buffered = sess.setdefault('in_seq_buffered', set())
    if not sess.get('in_seq_initialized'):
        sess['in_seq_at'] = (seq + 1) & PACKET_SEQ_MASK
        sess['in_seq_initialized'] = True
        buffered.clear()
        return

    in_seq = int(sess.get('in_seq_at', 0)) & PACKET_SEQ_MASK
    if seq >= in_seq:
        sess['in_seq_at'] = (seq + 1) & PACKET_SEQ_MASK
        buffered.clear()

def build_channel_packet(messages: bytes, sess: dict, reliable=False) -> bytes:
    # BigWorld С‚СЂРёРјР°С” Р”Р’Рђ РѕРєСЂРµРјРёС… Р»С–С‡РёР»СЊРЅРёРєРё sequence:
    #   - reliable РїР°РєРµС‚Рё    в†’ channel.useNextSequenceID()
    #   - non-reliable       в†’ networkInterface.getNextSequenceID()
    # РљР»С–С”РЅС‚СЃСЊРєРёР№ reliable-window РїРµСЂРµРІС–СЂСЏС” РїРѕСЃР»С–РґРѕРІРЅС–СЃС‚СЊ РўР†Р›Р¬РљР РґР»СЏ reliable.
    # РЇРєС‰Рѕ Р·РјС–С€Р°С‚Рё вЂ” reliable РїР°РєРµС‚Рё Р№РґСѓС‚СЊ Р· РїСЂРѕРїСѓСЃРєР°РјРё С– РєР»С–С”РЅС‚ С—С… Р±СѓС„РµСЂРёР·СѓС”
    # РЅР°Р·Р°РІР¶РґРё, РЅРµ РІРёРєРѕРЅСѓСЋС‡Рё. РўСЂРёРјР°С”РјРѕ РґРІР° РѕРєСЂРµРјРёС… counter'Р°.
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
    1: 4,    # authenticate
    2: 16,   # avatarUpdateImplicit: Coord + YawPitchRoll + refNum
    3: 25,   # avatarUpdateExplicit: spaceID + vehicleID + Coord + YPR + onGround + refNum
    4: 19,   # avatarUpdateWardImplicit: wardID + Coord + YPR
    5: 28,   # avatarUpdateWardExplicit: wardID + spaceID + vehicleID + Coord + YPR + onGround
    6: 1,    # ackPhysicsCorrection
    7: 5,    # ackWardPhysicsCorrection
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

# в”Ђв”Ђ Mercury packet helpers в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

def make_reply(reply_id: bytes, payload: bytes) -> bytes:
    """0xFF reply: flags(2) + 0xFF(1) + len(4) + reply_id(4) + payload"""
    body = reply_id + payload
    return b'\x00\x00\xff' + struct.pack('<I', len(body)) + body

def make_bundle(messages: bytes) -> bytes:
    """Plain serverв†’client bundle (no requests, no encryption)"""
    return b'\x00\x00' + messages

def msg_fixed(msg_id: int, payload: bytes) -> bytes:
    return bytes([msg_id]) + payload

def msg_varlen(msg_id: int, payload: bytes) -> bytes:
    return bytes([msg_id]) + struct.pack('<H', len(payload)) + payload

# в”Ђв”Ђ ClientInterface MsgIDs в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
#  0x00 authenticate          fixed  4 B  (uint32 key)
#  0x01 bandwidthNotification fixed  4 B  (uint32 bps)
#  0x02 updateFrequencyNotif  fixed  1 B  (uint8  hertz)
#  0x03 setGameTime           fixed  4 B  (uint32 gameTime)
#  0x04 resetEntities         fixed  1 B  (bool   keepPlayer)
#  0x05 createBasePlayer      varlen 2 B  EntityID(4)+EntityTypeID(2)+props
#  0x80+N entityMessage       varlen 2 B  EntityID(4)+methodArgs

ACCOUNT_ENTITY_TYPE = 0   # Account вЂ” 1-Р№ С‚РёРї Сѓ entities.xml (idx=0)
AVATAR_ENTITY_TYPE  = 1   # Avatar  вЂ” 2-Р№ С‚РёРї Сѓ entities.xml (idx=1).
                          # Р’РђР–Р›РР’Рћ: parse_def.py РґР°С” Р‘Р•Р— alphasort:
                          # 0=Account 1=Avatar 2=Admin 3=Arena 4=ArenaMgr
                          # 5=Vehicle 6=Projectile 7=AreaDestructibles ...
                          # РџРѕРјРёР»РєРѕРІРёР№ typeID=6 Р»Р°РјР°РІ createBasePlayer:
                          # РєР»С–С”РЅС‚ РЅР°РјР°РіР°РІСЃСЏ СЃС‚РІРѕСЂРёС‚Рё Projectile Р·
                          # Avatar-property stream в†’ C++ segfault.
VEHICLE_ENTITY_TYPE = 2   # CLIENT-SIDE clientIndex! РќР• server idx=5.
                          # entity_type.cpp:209 РІРёРєРѕСЂРёСЃС‚РѕРІСѓС” clientIndex,
                          # СЏРєРёР№ РљРћРњРџР Р•РЎРЈР„ С–РЅРґРµРєСЃРё (server-only entities
                          # Р±РµР· .pyc РЅР° РєР»С–С”РЅС‚С– вЂ” РїСЂРѕРїСѓСЃРєР°СЋС‚СЊСЃСЏ).
                          # РЈ WoT 0.6.5 РєР»С–С”РЅС‚ РјР°С” .pyc РґР»СЏ:
                          # Account, Avatar, Vehicle, AreaDestructibles,
                          # Flock, OfflineEntity в†’ clientIndex 0..5.
                          # Vehicle (server idx=5) в†’ clientIndex=2.
PLAYER_ENTITY_ID    = 1   # any non-zero EntityID for our player (Account)
AVATAR_ENTITY_ID    = 100 # РѕРєСЂРµРјРёР№ EntityID РґР»СЏ Avatar entity Сѓ Р±РѕСЋ
PLAYER_VEHICLE_ID   = 200 # EntityID РґР»СЏ Vehicle entity, РЅР° СЏРєРѕРјСѓ РіСЂР°С” player
SPACE_ID            = 1   # arbitrary SpaceID for hangar

# в”Ђв”Ђв”Ђ Account.def ClientMethods (РїРѕСЂСЏРґРѕРє Р· PackedSection РїР°СЂСЃРµСЂР°) в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
#   idx=0  version_eu6501      (РјР°СЂker, 0x81)
#   idx=1  onCmdResponse       0x82  (INT16 reqID, INT16 resultID)
#   idx=2  onCmdResponseExt    0x83  (INT16 reqID, INT16 resultID, STRING ext)
#   idx=3  onKickedFromServer  0x84
#   idx=4  onEnqueued          0x85  (no args)
#   idx=5  onEnqueueFailure    0x86  (INT8 errorCode)
#   idx=6  onDequeued          0x87  (no args)
#   idx=7  onArenaCreated      0x88  (no args)
#   idx=8  onArenaJoinFailure  0x89  (INT8 errorCode)
#   idx=12 onKickedFromQueue   0x8d  (no args)
#   idx=13 onKickedFromArena   0x8e  (INT8 reasonCode)
#   idx=15 showGUI             0x90  (STRING ctx)
#   idx=20 update              0x95  (STRING diff)
# Р’РђР–Р›РР’Рћ: СЂРµР°Р»СЊРЅРёР№ msgID = 0x81 + def_index (Mercury РїСЂРѕРїСѓСЃРєР°С” idx=0).
ACCOUNT_SHOWGUI_MSG_ID          = 0x90
ACCOUNT_RECEIVE_ACTIVE_ARENAS_MSG_ID = 0x91
ACCOUNT_RECEIVE_SERVER_STATS_MSG_ID = 0x92
ACCOUNT_RECEIVE_QUEUE_INFO_MSG_ID = 0x93
ACCOUNT_ONCMDRESPONSE_MSG_ID    = 0x82
ACCOUNT_ONCMDRESPONSEEXT_MSG_ID = 0x83
ACCOUNT_UPDATE_MSG_ID           = 0x95
ACCOUNT_ONENQUEUED_MSG_ID       = 0x85
ACCOUNT_ONDEQUEUED_MSG_ID       = 0x87
ACCOUNT_ONARENACREATED_MSG_ID   = 0x88
ACCOUNT_ONKICKEDFROMQUEUE_MSG_ID = 0x8d
AVATAR_UPDATEARENA_MSG_ID        = 0x92  # Avatar.updateArena(updateType, argStr)
AVATAR_UPDATE_VEHICLE_HEALTH_MSG_ID = 0x83
AVATAR_UPDATE_VEHICLE_RELOAD_MSG_ID = 0x84
AVATAR_UPDATE_VEHICLE_AMMO_MSG_ID = 0x85
AVATAR_UPDATE_VEHICLE_SETTING_MSG_ID = 0x86
AVATAR_UPDATE_TARGETING_INFO_MSG_ID = 0x87
AVATAR_UPDATE_GUN_MARKER_MSG_ID     = 0x88
AVATAR_UPDATE_OWN_POSITION_MSG_ID   = 0x89
AVATAR_SHOW_TRACER_MSG_ID           = 0x8c
AVATAR_STOP_TRACER_MSG_ID           = 0x8d
AVATAR_EXPLODE_PROJECTILE_MSG_ID    = 0x8e
AVATAR_ON_VEHICLE_LEFT_ARENA_MSG_ID = 0x91
AVATAR_UPDATE_POSITIONS_MSG_ID      = 0x93
VEHICLE_SHOW_SHOOTING_MSG_ID        = 0x81
VEHICLE_SHOW_DAMAGE_FROM_SHOT_MSG_ID = 0x82
VEHICLE_SHOW_DAMAGE_FROM_EXPLOSION_MSG_ID = 0x83
ENABLE_CLIENT_SHOT_DAMAGE_EFFECTS = bool(get_value(
    CONFIG, 'combat.client_shot_damage_effects', False))
CLIENT_LEAVE_AOI_MSG_ID = 0x0c
CLIENT_DETAILED_POSITION_MSG_ID     = 0x31
CLIENT_FORCED_POSITION_MSG_ID       = 0x32
CLIENT_CONTROL_ENTITY_MSG_ID        = 0x33
CLIENT_SET_VEHICLE_MSG_ID           = 0x10
CLIENT_AVATAR_UPDATE_NOALIAS_FULLPOS_YPR_MSG_ID = 0x11

# Account exposed BaseMethods (client в†’ server):
#   idx=3 msgID=0xc3  doCmdStr      (INT16 reqID, INT16 cmd, STRING)
#   idx=4 msgID=0xc4  doCmdInt3     (INT16 reqID, INT16 cmd, INT32, INT32, INT32)
#   idx=5 msgID=0xc5  doCmdInt4     (INT16 reqID, INT16 cmd, 4 x INT32)
#   idx=6 msgID=0xc6  doCmdInt2Str  (INT16 reqID, INT16 cmd, INT32, INT32, STRING)
#   idx=7 msgID=0xc7  doCmdIntArr   (INT16 reqID, INT16 cmd, INT32 array)
ACCOUNT_DOCMD_MSG_IDS = {0xc3, 0xc4, 0xc5, 0xc6, 0xc7}

# BigWorld native streaming (lib/connection/client_interface.hpp + common):
#   #55 = 0x37  resourceHeader   varlen2 B  (uint16 id, STRING desc)
#   #56 = 0x38  resourceFragment varlen2 B  (uint16 rid, uint8 seq,
#                                             uint8 flags, raw data)
# flags=1 в†’ final fragment (sets DataDownload.hasLast_=true, see
# server_connection.cpp:2168 вЂ” `pData->insert(seg, args.flags == 1)`).
# РћРґРёРЅ fragment Р· flags=1 + header в†’ onStreamComplete fires РѕРґСЂР°Р·Сѓ.
RESOURCE_HEADER_MSG_ID   = 0x37
RESOURCE_FRAGMENT_MSG_ID = 0x38

# WoT AccountCommands (Р· res/scripts/common/AccountCommands.py):
CMD_SYNC_DATA    = 100      # cmd=100 в†’ РґС–С„С„ (РЅР°С€ full_sync) Сѓ ext
CMD_EQUIP        = 101
CMD_EQUIP_OPTDEV = 102
CMD_EQUIP_SHELLS = 103
CMD_EQUIP_EQS    = 104
CMD_EQUIP_TMAN   = 105
CMD_SYNC_SHOP    = 300      # cmd=300 в†’ Р’Р•Р›РРљРР™ shop dict С‡РµСЂРµР· STREAM
CMD_BUY_ITEM     = 302
CMD_BUY_TMAN     = 303
CMD_DISMISS_TMAN = 306
CMD_SYNC_DOSSIERS = 600
CMD_SET_LANGUAGE = 1000
CMD_REQ_ARENA_LIST = 500
CMD_REQ_QUEUE_INFO = 502
CMD_REQ_SERVER_STATS = 501
CMD_ENQUEUE_FOR_ARENA = 700  # vehInvID, arenaTypeID, queueType
CMD_DEQUEUE      = 701
ACCOUNT_KNOWN_COMMANDS = {
    CMD_SYNC_DATA,
    CMD_EQUIP,
    CMD_EQUIP_OPTDEV,
    CMD_EQUIP_SHELLS,
    CMD_EQUIP_EQS,
    CMD_EQUIP_TMAN,
    CMD_SYNC_SHOP,
    CMD_BUY_ITEM,
    CMD_BUY_TMAN,
    CMD_DISMISS_TMAN,
    CMD_SYNC_DOSSIERS,
    CMD_SET_LANGUAGE,
    CMD_REQ_ARENA_LIST,
    CMD_REQ_SERVER_STATS,
    CMD_REQ_QUEUE_INFO,
    CMD_ENQUEUE_FOR_ARENA,
    CMD_DEQUEUE,
}
RES_SUCCESS      = 0
RES_STREAM       = 1
RES_CACHE        = 2
RES_FAILURE      = -1
RES_WRONG_ARGS   = -2
RES_NON_PLAYER   = -3
RES_SHOP_DESYNC  = -4
LOCK_REASON_NONE = 0
LOCK_REASON_ON_ARENA = 16
LOCK_REASON_ROSTER = 32
LOCK_REASON_IN_QUEUE = 1 | LOCK_REASON_ROSTER


def bw_pack_int(value: int) -> bytes:
    if value >= 0xFF:
        return b'\xff' + bytes((value & 0xFF, (value >> 8) & 0xFF, (value >> 16) & 0xFF))
    return bytes((value,))


def bw_pack_string(data: bytes) -> bytes:
    return bw_pack_int(len(data)) + data

def build_init_bundle(session_key_int: int, account_name: str = 'qwerty',
                      database_id: int = 1) -> bytes:
    msgs = b''

    # 0x00 authenticate вЂ“ server proves it knows the session key
    msgs += msg_fixed(0x00, struct.pack('<I', session_key_int))

    # 0x01 bandwidthNotification вЂ“ 64 KB/s
    msgs += msg_fixed(0x01, struct.pack('<I', 64000))

    msgs += msg_fixed(0x02, struct.pack('<B', int(1.0 / BATTLE_MOTION_TICK)))

    # 0x03 setGameTime вЂ“ tick 0
    msgs += msg_fixed(0x03, struct.pack('<I', 0))

    # 0x04 resetEntities вЂ“ don't keep player
    msgs += msg_fixed(0x04, struct.pack('<B', 0))

    # 0x05 createBasePlayer
    #   EntityID(4 LE) + EntityTypeID(2 LE) + base+client property stream
    #   Account РјР°С” 3 BASE_AND_CLIENT РІР»Р°СЃС‚РёРІРѕСЃС‚С– (РїРѕСЂСЏРґРѕРє СЏРє Сѓ Account.def):
    #     1. name           (STRING)
    #     2. normalizedName (STRING)
    #     3. serverSettings (PYTHON  в†’  pickled dict)
    #   serverSettings РџРћР’РРќР•Рќ РјС–СЃС‚РёС‚Рё vivoxDomain/vivoxIssuer/voipDomain вЂ”
    #   С–РЅР°РєС€Рµ Account.onBecomePlayer РїР°РґР°С” Р· 'NoneType is unsubscriptable'
    #   (РґРёРІ. World_of_Tanks/res/scripts/client/Account.py:142).
    #   captchaKey РќР• РїРѕС‚СЂС–Р±РµРЅ: CaptchaUI.isCaptchaRequired() Р±Р°Р·СѓС”С‚СЊСЃСЏ
    #   Р»РёС€Рµ РЅР° _battlesTillCaptcha (Stats _CACHE_STATS), СЏРєРµ РјРё Р·Р°РґР°С”РјРѕ
    #   РІ diff['cache']['battlesTillCaptcha'] = 999.
    server_settings_pickle = pickle.dumps({
        'vivoxDomain':  '',
        'vivoxIssuer':  '',
        'voipDomain':   '',
        'serverUTC':    0,
    }, protocol=0)
    props = b''
    account_name_bytes = account_name.encode('utf-8', 'ignore') or b'player'
    props += bw_pack_string(account_name_bytes)      # name
    props += bw_pack_string(account_name_bytes.lower())  # normalizedName
    props += bw_pack_string(server_settings_pickle)  # serverSettings (PYTHON)

    cbp_payload = struct.pack('<I', PLAYER_ENTITY_ID) + \
                  struct.pack('<H', ACCOUNT_ENTITY_TYPE) + \
                  props
    msgs += msg_varlen(0x05, cbp_payload)

    # Account.showGUI(ctx)  вЂ” entityMessage, msgID=0x90
    # РўСЂРёРіРµСЂРёС‚СЊ WindowsManager.showLobby в†’ CommonPage.processLobby.
    showgui_ctx = (
        f"(dp0\nS'databaseID'\np1\nL{int(database_id)}L\n"
        "sS'serverUTC'\np2\nL0L\ns."
    ).encode('ascii')
    em = struct.pack('<I', PLAYER_ENTITY_ID) + bw_pack_string(showgui_ctx)
    msgs += msg_varlen(ACCOUNT_SHOWGUI_MSG_ID, em)

    return msgs


def build_oncmdrespext(req_id: int, result_id: int, ext_pickle: bytes) -> bytes:
    """Account.onCmdResponseExt(reqID, resultID, ext) вЂ” entityMessage 0x83."""
    em = struct.pack('<I', PLAYER_ENTITY_ID)
    em += struct.pack('<hh', req_id, result_id)  # INT16, INT16
    em += bw_pack_string(ext_pickle)             # STRING (PYTHON pickle)
    return msg_varlen(ACCOUNT_ONCMDRESPONSEEXT_MSG_ID, em)

def build_account_update(diff: dict) -> bytes:
    em = struct.pack('<I', PLAYER_ENTITY_ID)
    em += bw_pack_string(pickle.dumps(diff, protocol=2))
    return msg_varlen(ACCOUNT_UPDATE_MSG_ID, em)

def build_account_receive_server_stats(stats: dict) -> bytes:
    em = struct.pack('<I', PLAYER_ENTITY_ID)
    em += struct.pack('<II',
                      int(stats.get('playersCount', 0)),
                      int(stats.get('arenasCount', 0)))
    return msg_varlen(ACCOUNT_RECEIVE_SERVER_STATS_MSG_ID, em)

def build_account_receive_active_arenas():
    with battle_lock:
        players = len(active_battle_accounts)
        battles = 1 if players else 0
    arenas = []
    if battles:
        arena_type_id = normalize_arena_type_id(ARENA_TYPE_FALLBACK)
        arenas.append({
            'arenaID': 1,
            'arenaTypeID': arena_type_id,
            'roundLength': BATTLE_TIMER_SECONDS,
            'players': players,
        })
    payload = struct.pack('<I', PLAYER_ENTITY_ID)
    payload += bw_pack_int(len(arenas))
    for arena in arenas:
        payload += struct.pack('<IIIf',
                               int(arena['arenaID']),
                               int(arena['arenaTypeID']),
                               int(arena['roundLength']),
                               0.0)
    return msg_varlen(ACCOUNT_RECEIVE_ACTIVE_ARENAS_MSG_ID, payload)

def get_vehicle_level(vehicle: dict) -> int:
    try:
        level = int((vehicle or {}).get('level', 1))
    except (TypeError, ValueError, OverflowError):
        level = 1
    return max(1, min(10, level))

def get_vehicle_queue_class(vehicle: dict) -> str:
    vehicle = vehicle or {}
    vclass = str(vehicle.get('vehicleClass') or '')
    if vclass in VEHICLE_CLASS_TAGS:
        return vclass
    tags = vehicle.get('tags', [])
    for t in ('lightTank', 'mediumTank', 'heavyTank', 'SPG', 'AT-SPG'):
        if t in tags:
            return t
    return 'lightTank'

def get_queued_vehicle_info(sess):
    vehicle = sess.get('battle_vehicle')
    if not vehicle:
        return None
    return get_vehicle_level(vehicle), get_vehicle_queue_class(vehicle)

def get_matchmaking_queue_stats():
    levels = [0] * 11
    classes = [0] * 5
    length = 0
    with queue_lock:
        for queued in matchmaking_queue:
            q_sess = queued.get('sess')
            if q_sess:
                info = get_queued_vehicle_info(q_sess)
                if info:
                    level, vclass = info
                    if 1 <= level <= 10:
                        levels[level] += 1
                    class_idx = {'heavyTank': 0, 'mediumTank': 1, 'lightTank': 2, 'SPG': 3, 'AT-SPG': 4}.get(vclass, 2)
                    classes[class_idx] += 1
                    length += 1
    if MATCHMAKING_QUEUE_FAKE_FILLERS > 0 and length > 0:
        user_level = 1
        for lvl, cnt in enumerate(levels):
            if cnt > 0:
                user_level = lvl
                break
        fake_pool = [
            (user_level, 2), (user_level, 2), (user_level, 2), (user_level, 2),
            (user_level, 1), (user_level, 1), (user_level, 1), (user_level, 1),
            (max(1, user_level - 1), 0), (max(1, user_level - 1), 0),
            (max(1, user_level - 1), 0), (max(1, user_level - 1), 3),
            (min(10, user_level + 1), 3), (min(10, user_level + 1), 4),
        ]
        for level, class_idx in fake_pool[:MATCHMAKING_QUEUE_FAKE_FILLERS]:
            levels[level] += 1
            classes[class_idx] += 1
            length += 1
    return length, levels, classes

def build_account_receive_queue_info(randoms_length=0, randoms_levels=None, randoms_classes=None, companies_length=0):
    payload = struct.pack('<I', PLAYER_ENTITY_ID)
    payload += b'\x01'
    payload += struct.pack('<I', randoms_length)
    if randoms_levels is None:
        randoms_levels = [0] * 11
    payload += pack_uint32_array(randoms_levels)
    if randoms_classes is None:
        randoms_classes = [0] * 5
    payload += pack_uint32_array(randoms_classes)
    payload += b'\x01'
    payload += struct.pack('<I', companies_length)
    return msg_varlen(ACCOUNT_RECEIVE_QUEUE_INFO_MSG_ID, payload)

def build_account_server_counters_update():
    stats = get_server_stats()
    return build_account_receive_server_stats(stats)


def parse_doCmd_request(msg_id: int, payload: bytes):
    """Р’РёС‚СЏРіРЅСѓС‚Рё (reqID, cmd) Р· doCmd* РїР°РєРµС‚Сѓ. Args РїРѕС‡РёРЅР°СЋС‚СЊСЃСЏ Р· INT16+INT16."""
    if len(payload) < 4:
        return None, None
    req_id, cmd = struct.unpack_from('<hh', payload, 0)
    return req_id, cmd


def account_doCmd_payload_cmd(msg_id: int, payload: bytes):
    if msg_id not in ACCOUNT_DOCMD_MSG_IDS:
        return None
    if msg_id == 0xc4 and len(payload) < 16:
        return None
    if msg_id == 0xc5 and len(payload) < 20:
        return None
    if msg_id == 0xc7 and len(payload) < 8:
        return None
    _req_id, cmd = parse_doCmd_request(msg_id, payload)
    if cmd in ACCOUNT_KNOWN_COMMANDS:
        return cmd
    return None


def parse_doCmd_int3(payload: bytes):
    if len(payload) < 16:
        return None
    return struct.unpack_from('<iii', payload, 4)


def parse_doCmd_int4(payload: bytes):
    if len(payload) < 20:
        return None
    return struct.unpack_from('<iiii', payload, 4)


def parse_doCmd_int_arr(payload: bytes):
    if len(payload) < 8:
        return []
    count = struct.unpack_from('<I', payload, 4)[0]
    arr = []
    offset = 8
    for _ in range(min(count, 1024)):
        if offset + 4 > len(payload):
            break
        arr.append(struct.unpack_from('<i', payload, offset)[0])
        offset += 4
    return arr


MAX_VEHICLES_INLINE = get_value(CONFIG, 'data.max_vehicles_inline', None)
VEHICLE_CLASS_TAGS = ('lightTank', 'mediumTank', 'heavyTank', 'SPG', 'AT-SPG')

VEHICLE_MAX_HEALTH_OVERRIDES = dict(get_value(
    CONFIG, 'combat.vehicle_max_health_overrides', {'Lowe': 1650}) or {})

VEHICLE_SPEED_MULTIPLIER = float(get_value(
    CONFIG, 'combat.vehicle_speed_multiplier', 1.0))
VEHICLE_ROTATION_MULTIPLIER = float(get_value(
    CONFIG, 'combat.vehicle_rotation_multiplier', 1.5))


def make_default_ammo_from_shells(shells, max_ammo):
    shells = list(shells or [])
    max_ammo = int(max_ammo or 0)
    if max_ammo <= 0:
        max_ammo = 60
    ammo = []
    current = 0
    for shell in shells:
        compact = int(shell.get('compactDescr', 0))
        if not compact:
            continue
        count = int(float(shell.get('defaultPortion', 0.0)) * max_ammo + 0.5)
        if current + count > max_ammo:
            count = max_ammo - current
        current += count
        ammo.extend([compact, max(0, count)])
    if ammo and current <= 0:
        ammo[1] = max_ammo
    elif ammo and current < max_ammo:
        ammo[1] += max_ammo - current
    return ammo


def normalize_vehicle_tags(vehicle: dict):
    tags = vehicle.get('tags') if isinstance(vehicle, dict) else None
    if tags is None:
        tags = []
    return frozenset(str(tag) for tag in tags)


def vehicle_class_from_tags(tags) -> str:
    tags = normalize_vehicle_tags({'tags': tags})
    for tag in VEHICLE_CLASS_TAGS:
        if tag in tags:
            return tag
    return ''


def is_artillery_vehicle(vehicle: dict) -> bool:
    if not vehicle:
        return False
    if bool(vehicle.get('isSPG')):
        return True
    vehicle_class = str(vehicle.get('vehicleClass') or '')
    if vehicle_class:
        return vehicle_class == 'SPG'
    return 'SPG' in normalize_vehicle_tags(vehicle)


def is_at_spg_vehicle(vehicle: dict) -> bool:
    if not vehicle:
        return False
    if bool(vehicle.get('isATSPG')):
        return True
    vehicle_class = str(vehicle.get('vehicleClass') or '')
    if vehicle_class:
        return vehicle_class == 'AT-SPG'
    return 'AT-SPG' in normalize_vehicle_tags(vehicle)


def is_hull_aim_autorotation_vehicle(vehicle: dict) -> bool:
    """Vehicles that should rotate the hull toward the aim point when standing
    still: SPG (artillery) and AT-SPG (tank destroyers).  Both have limited or
    no turret traverse and benefit from hull autorotation in strategic and
    sniper sights alike."""
    return is_artillery_vehicle(vehicle) or is_at_spg_vehicle(vehicle)


def is_hull_locked_gun_vehicle(vehicle: dict) -> bool:
    return is_at_spg_vehicle(vehicle) and not is_artillery_vehicle(vehicle)


_MOTION_WARNED = False
_VEHICLES_CACHE = {}
_VEHICLES_BY_INV_CACHE = {}


def load_all_vehicles(include_disabled: bool = False):
    """Р§РёС‚Р°С” _vehicles.json (РіРµРЅРµСЂСѓС”С‚СЊСЃСЏ _dump_vehicles.py) С– РїРѕРІРµСЂС‚Р°С”
    СЃРїРёСЃРѕРє (invID, compactDescr_bytes, name) РґР»СЏ СѓСЃС–С… С‚Р°РЅРєС–РІ РіСЂРё.
    РћР±РјРµР¶СѓС” РґРѕ MAX_VEHICLES_INLINE С‰РѕР± pickle РІРјС–СЃС‚РёРІСЃСЏ РІ РѕРґРёРЅ UDP packet."""
    cache_key = bool(include_disabled)
    cached = _VEHICLES_CACHE.get(cache_key)
    if cached is not None:
        return cached
    json_path = resolve_existing_path(
        ROOT_DIR, get_value(CONFIG, 'data.vehicles_path', 'data/_vehicles.json'),
        '_vehicles.json')
    if not os.path.exists(json_path):
        print(f"[!] _vehicles.json РЅРµ Р·РЅР°Р№РґРµРЅРѕ вЂ” Р°РЅРіР°СЂ Р±СѓРґРµ РїРѕСЂРѕР¶РЅС–Рј")
        _VEHICLES_CACHE[cache_key] = []
        _VEHICLES_BY_INV_CACHE[cache_key] = {}
        return _VEHICLES_CACHE[cache_key]
    import json
    with open(json_path, 'r') as f:
        data = json.load(f)
    out = []
    vehicles_data = data['vehicles']
    if not include_disabled:
        vehicles_data = [
            vehicle for vehicle in vehicles_data
            if vehicle.get('enabled', True) and not vehicle.get('disabled', False)
        ]
    if MAX_VEHICLES_INLINE is not None:
        vehicles_data = vehicles_data[:MAX_VEHICLES_INLINE]
    for inv_id, v in enumerate(vehicles_data, start=1):
        cd = bytes.fromhex(v['compactDescr_hex'])
        crew_size = v.get('crewSize', 4)
        shells = list(v.get('shells', []))
        default_ammo = list(v.get('defaultAmmo', []))
        if sum(int(q) for q in default_ammo[1::2]) <= 0 and shells:
            default_ammo = make_default_ammo_from_shells(shells, v.get('maxAmmo', 0))
        level = get_vehicle_level(v)
        max_health = int(v.get('maxHealth', 1000))
        max_health = max(max_health, int(VEHICLE_MAX_HEALTH_OVERRIDES.get(v['name'], 0)))
        scaled_speed_limits = [
            float(limit or 0.0) * VEHICLE_SPEED_MULTIPLIER
            for limit in (v.get('speedLimits') or [])
        ]
        tags = normalize_vehicle_tags(v)
        vehicle_class = str(v.get('vehicleClass') or vehicle_class_from_tags(tags))
        out.append({
            'inv_id': inv_id,
            'compactDescr': cd,
            'name': v['name'],
            'level': level,
            'nation': v.get('nation') or 'ussr',
            'nationID': int(v.get('nationID') or 0),
            'vehicleTypeID': int(v.get('vehicleTypeID') or 0),
            'tags': tags,
            'vehicleClass': vehicle_class,
            'isSPG': bool(v.get('isSPG')) or vehicle_class == 'SPG',
            'isATSPG': bool(v.get('isATSPG')) or vehicle_class == 'AT-SPG',
            'crewSize': crew_size,
            'turretCompactDescr': v.get('turretCompactDescr', 0),
            'gunCompactDescr': v.get('gunCompactDescr', 0),
            'maxHealth': max_health,
            'speedLimits': scaled_speed_limits,
            'chassisRotationSpeed': float(v.get('chassisRotationSpeed') or 0.0) * VEHICLE_ROTATION_MULTIPLIER,
            'chassisRotationSpeedLimit': float(v.get('chassisRotationSpeedLimit') or 0.0) * VEHICLE_ROTATION_MULTIPLIER,
            'chassisMaxClimbAngleRad': float(v.get('chassisMaxClimbAngleRad') or 0.0),
            'chassisMaxLoadKg': float(v.get('chassisMaxLoadKg') or 0.0),
            'chassisRotationIsAroundCenter': bool(v.get('chassisRotationIsAroundCenter') or False),
            'turretRotationSpeed': float(v.get('turretRotationSpeed', 8.0)),
            'gunRotationSpeed': float(v.get('gunRotationSpeed', 8.0)),
            'reloadTime': float(v.get('reloadTime', 5.0)),
            'circularVisionRadius': float(v.get('circularVisionRadius') or 0.0),
            'invisibilityMoving': float(v.get('invisibilityMoving') or 0.0),
            'invisibilityStill': float(v.get('invisibilityStill') or 0.0),
            'gunInvisibilityFactorAtShot': float(v.get('gunInvisibilityFactorAtShot') or 0.0),
            'enginePower': float(v.get('enginePower') or 0.0),
            'totalWeightKg': float(v.get('totalWeightKg') or 0.0),
            'hpPerTon': float(v.get('hpPerTon') or 0.0),
            'hullWeightKg': float(v.get('hullWeightKg') or 0.0),
            'chassisWeightKg': float(v.get('chassisWeightKg') or 0.0),
            'turretWeightKg': float(v.get('turretWeightKg') or 0.0),
            'engineWeightKg': float(v.get('engineWeightKg') or 0.0),
            'fuelWeightKg': float(v.get('fuelWeightKg') or 0.0),
            'radioWeightKg': float(v.get('radioWeightKg') or 0.0),
            'gunWeightKg': float(v.get('gunWeightKg') or 0.0),
            'baseWeightKg': float(v.get('baseWeightKg') or 0.0),
            'terrainResistance': list(v.get('terrainResistance', [])),
            'brakeForce': float(v.get('brakeForce') or 0.0),
            'specificFriction': float(v.get('specificFriction') or 0.0),
            'chassisMinPlaneNormalY': float(v.get('chassisMinPlaneNormalY') or 0.0),
            'trackCenterOffset': float(v.get('trackCenterOffset') or 0.0),
            'rotationEnergy': float(v.get('rotationEnergy') or 0.0),
            'rotationSpeedLimit': float(v.get('rotationSpeedLimit') or 0.0),
            'armorModel': dict(v.get('armorModel') or {}),
            'defaultAmmo': default_ammo,
            'shells': shells,
        })
    global _MOTION_WARNED
    if not _MOTION_WARNED:
        bad = []
        for v in out:
            limits = v.get('speedLimits') or ()
            issues = []
            if len(limits) < 2 or float(limits[0] or 0.0) <= 0.0 or float(limits[1] or 0.0) <= 0.0:
                issues.append('speedLimits')
            if float(v.get('hpPerTon') or 0.0) <= 0.0:
                issues.append('hpPerTon')
            if float(v.get('chassisRotationSpeed') or 0.0) <= 0.0:
                issues.append('chassisRotationSpeed')
            resistance = v.get('terrainResistance') or ()
            if len(resistance) < 3 or float(resistance[0] or 0.0) <= 0.0:
                issues.append('terrainResistance')
            if float(v.get('brakeForce') or 0.0) <= 0.0:
                issues.append('brakeForce')
            if float(v.get('rotationSpeedLimit') or 0.0) <= 0.0:
                issues.append('rotationSpeedLimit')
            if issues:
                bad.append((v['name'], issues))
        if bad:
            preview = ', '.join(f"{name}({'+'.join(issues)})" for name, issues in bad[:6])
            print(f"[!] {len(bad)} vehicle(s) missing motion params -> {preview}"
                  f"{' ...' if len(bad) > 6 else ''}; "
                  f"battle physics will use BATTLE_DEFAULT_* fallbacks for them "
                  f"(re-run _dump_vehicles.py if this is unexpected)")
        else:
            print(f"[i] motion params OK for all {len(out)} vehicle(s) "
                  f"(speedLimits/engine/chassis/terrain physics loaded from XML)")
        _MOTION_WARNED = True
    _VEHICLES_CACHE[cache_key] = out
    _VEHICLES_BY_INV_CACHE[cache_key] = {
        int(vehicle['inv_id']): vehicle for vehicle in out
    }
    return out


def collect_shell_prices(veh_list):
    """Return {nationID: {shellCompactDescr: priceTuple}} for loaded vehicles."""
    shell_prices = {}
    for vehicle in veh_list:
        for shell in vehicle.get('shells', []):
            compact = int(shell.get('compactDescr', 0))
            if not compact:
                continue
            nation_id = (compact >> 4) & 15
            price = shell.get('price', (0, 0))
            shell_prices.setdefault(nation_id, {})[compact] = tuple(price)
    return shell_prices



_TANKMEN_CONFIG_CACHE = None
_ARTEFACTS_CONFIG_CACHE = None

ITEM_TYPE_VEHICLE         = 1
ITEM_TYPE_CHASSIS         = 2
ITEM_TYPE_TURRET          = 3
ITEM_TYPE_GUN             = 4
ITEM_TYPE_ENGINE          = 5
ITEM_TYPE_FUEL_TANK       = 6
ITEM_TYPE_RADIO           = 7
ITEM_TYPE_TANKMAN         = 8
ITEM_TYPE_OPTIONAL_DEVICE = 9
ITEM_TYPE_SHELL           = 10
ITEM_TYPE_EQUIPMENT       = 11

NATION_NONE_INDEX = 15

_COMPONENT_TYPE_MAP = {
    'chassis':  ITEM_TYPE_CHASSIS,
    'engines':  ITEM_TYPE_ENGINE,
    'fuelTanks': ITEM_TYPE_FUEL_TANK,
    'radios':   ITEM_TYPE_RADIO,
    'turrets':  ITEM_TYPE_TURRET,
    'guns':     ITEM_TYPE_GUN,
}

_ALL_UNLOCK_DESCRIPTORS_CACHE = None


def generate_all_unlock_descriptors():
    global _ALL_UNLOCK_DESCRIPTORS_CACHE
    if _ALL_UNLOCK_DESCRIPTORS_CACHE is not None:
        return _ALL_UNLOCK_DESCRIPTORS_CACHE
    import json as _json
    json_path = resolve_existing_path(
        ROOT_DIR, get_value(CONFIG, 'data.vehicles_path', 'data/_vehicles.json'),
        '_vehicles.json')
    if not os.path.exists(json_path):
        return set()
    with open(json_path, 'r') as f:
        data = _json.load(f)
    unlocks = set()
    for v in data.get('vehicles', []):
        nation_id = int(v.get('nationID', 0))
        vtype_id = int(v.get('vehicleTypeID', 0))
        unlocks.add((vtype_id << 8) | (nation_id << 4) | ITEM_TYPE_VEHICLE)
    components = data.get('components', {})
    for nation_id_str, comp_data in components.items():
        nation_id = int(nation_id_str)
        for comp_key, item_type in _COMPONENT_TYPE_MAP.items():
            for comp_id in comp_data.get(comp_key, []):
                comp_id = int(comp_id)
                if comp_id > 0 and comp_id < 65535:
                    unlocks.add((comp_id << 8) | (nation_id << 4) | item_type)
    _ALL_UNLOCK_DESCRIPTORS_CACHE = unlocks
    print(f"[*] generate_all_unlock_descriptors: {len(unlocks)} items")
    return unlocks

ROLE_COMMANDER = 1
ROLE_RADIOMAN  = 2
ROLE_DRIVER    = 3
ROLE_GUNNER    = 4
ROLE_LOADER    = 5

DEFAULT_CREW_ROLES = (ROLE_COMMANDER, ROLE_DRIVER, ROLE_GUNNER,
                      ROLE_RADIOMAN, ROLE_LOADER, ROLE_LOADER)


def load_tankmen_config():
    global _TANKMEN_CONFIG_CACHE
    if _TANKMEN_CONFIG_CACHE is not None:
        return _TANKMEN_CONFIG_CACHE
    path = resolve_existing_path(
        ROOT_DIR, get_value(CONFIG, 'data.tankmen_path', 'data/_tankmen.json'),
        '_tankmen.json')
    if not os.path.exists(path):
        print('[!] _tankmen.json not found - run _dump_tankmen.py first')
        _TANKMEN_CONFIG_CACHE = {}
        return _TANKMEN_CONFIG_CACHE
    import json
    with open(path, 'r', encoding='utf-8') as f:
        _TANKMEN_CONFIG_CACHE = json.load(f)
    return _TANKMEN_CONFIG_CACHE


def load_artefacts_config():
    global _ARTEFACTS_CONFIG_CACHE
    if _ARTEFACTS_CONFIG_CACHE is not None:
        return _ARTEFACTS_CONFIG_CACHE
    path = resolve_existing_path(
        ROOT_DIR, get_value(CONFIG, 'data.artefacts_path',
                            'data/_artefacts.json'),
        '_artefacts.json')
    if not os.path.exists(path):
        print('[!] _artefacts.json not found - run _dump_artefacts.py first')
        _ARTEFACTS_CONFIG_CACHE = {'equipments': [], 'optional_devices': []}
        return _ARTEFACTS_CONFIG_CACHE
    import json
    with open(path, 'r', encoding='utf-8') as f:
        _ARTEFACTS_CONFIG_CACHE = json.load(f)
    return _ARTEFACTS_CONFIG_CACHE


def reset_data_caches():
    global _TANKMEN_CONFIG_CACHE, _ARTEFACTS_CONFIG_CACHE, _MOTION_WARNED, _ALL_UNLOCK_DESCRIPTORS_CACHE
    _TANKMEN_CONFIG_CACHE = None
    _ARTEFACTS_CONFIG_CACHE = None
    _ALL_UNLOCK_DESCRIPTORS_CACHE = None
    _MOTION_WARNED = False
    _VEHICLES_CACHE.clear()
    _VEHICLES_BY_INV_CACHE.clear()
    STATIC_OBSTACLE_CACHE.clear()
    STATIC_OBSTACLE_INDEX_CACHE.clear()
    SPOTTING_BUSH_CACHE.clear()
    SPOTTING_BUSH_INDEX_CACHE.clear()
    STATIC_MODEL_RADIUS_CACHE.clear()
    STATIC_MODEL_BOUNDS_CACHE.clear()
    BATTLE_TERRAIN_BLOCK_CACHE.clear()
    BATTLE_TERRAIN_WARNED.clear()


def make_artefact_compact_descr(item_type: int, in_nation_id: int,
                                nation_id: int = NATION_NONE_INDEX) -> int:
    header = (item_type & 0x0f) | ((nation_id & 0x0f) << 4)
    return header | ((in_nation_id & 0xffff) << 8)


def make_vehicle_compact_descr_with_optional_devices(
        compact_descr: bytes, device_slots: list) -> bytes:
    cd = bytes(compact_descr or b'')
    if len(cd) < 15:
        return cd
    optional_devices = b''
    optional_devices_mask = 0
    for device_cd in list(device_slots or [])[:3]:
        optional_devices_mask <<= 1
        device_cd = int(device_cd or 0)
        if device_cd > 0:
            device_id = (device_cd >> 8) & 0xffff
            optional_devices = struct.pack('<H', device_id) + optional_devices
            optional_devices_mask |= 1
    flags = (cd[14] & 0xf0) | (optional_devices_mask & 0x0f)
    return cd[:14] + bytes([flags]) + optional_devices + cd[15:]


def make_tankman_compact_descr(nation_id: int, vehicle_type_id: int,
                               role_id: int, role_level: int,
                               first_name_id: int, last_name_id: int,
                               icon_id: int, is_female: bool = False,
                               is_premium: bool = False,
                               free_xp: int = 0,
                               skills: list = None,
                               last_skill_level: int = 0) -> bytes:
    skills = list(skills or [])
    header = (ITEM_TYPE_TANKMAN & 0x0f) | ((nation_id & 0x0f) << 4)
    cd = struct.pack('4B', header, vehicle_type_id, role_id, role_level)
    cd += struct.pack(f'{1 + len(skills)}B', len(skills), *skills)
    cd += bytes([last_skill_level if skills else 0])
    MIN_ROLE_LEVEL, LEVELS_PER_RANK = 50, 50
    rank_offset = max(0, role_level - MIN_ROLE_LEVEL)
    rank, levels_into_rank = divmod(rank_offset, LEVELS_PER_RANK)
    levels_to_next_rank = LEVELS_PER_RANK - levels_into_rank
    flags = (1 if is_female else 0) | ((1 if is_premium else 0) << 1)
    cd += struct.pack('<B3H2Bi',
                      flags, first_name_id, last_name_id, icon_id,
                      rank, levels_to_next_rank, free_xp)
    return cd


def pick_random_passport(nation_name: str, is_premium: bool = False):
    cfg = load_tankmen_config().get(nation_name)
    if not cfg:
        return None
    groups = cfg.get('premiumGroups' if is_premium else 'normalGroups') or []
    if not groups:
        groups = cfg.get('normalGroups') or []
    if not groups:
        return None
    group = random.choice(groups)
    first_names = group.get('firstNames') or []
    last_names = group.get('lastNames') or []
    icons = group.get('icons') or []
    if not (first_names and last_names and icons):
        return None
    fn = random.choice(first_names)
    ln = random.choice(last_names)
    ic = random.choice(icons)
    return {
        'is_female': bool(group.get('isFemale')),
        'first_name_id': int(fn[0]),
        'last_name_id':  int(ln[0]),
        'icon_id':       int(ic[0]),
        'first_name':    fn[1],
        'last_name':     ln[1],
    }


def get_vehicle_inventory_map(include_disabled: bool = False):
    cache_key = bool(include_disabled)
    if cache_key not in _VEHICLES_BY_INV_CACHE:
        load_all_vehicles(include_disabled=include_disabled)
    return _VEHICLES_BY_INV_CACHE.get(cache_key, {})


def get_vehicle_by_inventory_id(inv_id: int):
    try:
        inv_id = int(inv_id)
    except (TypeError, ValueError):
        return None
    return get_vehicle_inventory_map().get(inv_id)


def get_vehicle_compact_descr(inv_id: int = None) -> bytes:
    vehicle = get_vehicle_by_inventory_id(inv_id) if inv_id else None
    if vehicle is not None:
        return vehicle['compactDescr']
    veh_list = load_all_vehicles()
    if veh_list:
        return veh_list[0]['compactDescr']
    return b'\x00' * 22


def set_session_battle_vehicle_snapshot(sess: dict, vehicle: dict,
                                        inv_id: int) -> dict:
    if not vehicle:
        return {}
    fwd, bwd = get_vehicle_speed_limits(vehicle)
    max_health = get_vehicle_max_health(vehicle)
    sess['battle_vehicle'] = vehicle
    sess['battle_vehicle_inv_id'] = int(inv_id)
    sess['battle_vehicle_compactDescr'] = vehicle.get('compactDescr') or b''
    sess['battle_vehicle_name'] = vehicle.get('name') or 'unknown'
    sess['battle_vehicle_speedLimits'] = (fwd, bwd)
    sess['battle_vehicle_maxHealth'] = max_health
    return vehicle


def get_session_battle_vehicle(sess: dict) -> dict:
    vehicle = sess.get('battle_vehicle')
    if vehicle:
        return vehicle
    requested_inv_id = int(sess.get('battle_vehicle_inv_id') or 1)
    vehicle = get_vehicle_by_inventory_id(requested_inv_id)
    if vehicle is None:
        print(f"    [!] missing battle vehicle invID={requested_inv_id} "
              f"for {sess.get('username') or 'player'}")
        return {}
    if vehicle:
        set_session_battle_vehicle_snapshot(sess, vehicle, requested_inv_id)
    return vehicle or {}


def is_artillery_session(sess: dict) -> bool:
    return is_artillery_vehicle(get_session_battle_vehicle(sess))


def get_vehicle_max_health(vehicle: dict) -> int:
    return max(1, int((vehicle or {}).get('maxHealth', 1000)))


def get_vehicle_speed_limits(vehicle: dict):
    limits = (vehicle or {}).get('speedLimits') or ()
    if len(limits) >= 2:
        forward = float(limits[0] or 0.0)
        backward = float(limits[1] or 0.0)
        if forward > 0.0 and backward > 0.0:
            return forward, backward
    if vehicle and vehicle.get('name'):
        print(f"[!] HARD vehicle speedLimits invalid for {vehicle.get('name')}; "
              f"falling back to battle defaults — fix _vehicles.json dump")
    return BATTLE_DEFAULT_FORWARD_SPEED, BATTLE_DEFAULT_BACKWARD_SPEED


def get_vehicle_accel_floor(vehicle: dict) -> float:
    forward_limit, _backward_limit = get_vehicle_speed_limits(vehicle)
    weight_t = float((vehicle or {}).get('totalWeightKg') or 0.0) / 1000.0
    if weight_t <= 25.0:
        target_time = BATTLE_ACCEL_LIGHT_TOP_SPEED_TIME
    elif weight_t >= 55.0:
        target_time = BATTLE_ACCEL_HEAVY_TOP_SPEED_TIME
    else:
        target_time = BATTLE_ACCEL_MEDIUM_TOP_SPEED_TIME
    return max(BATTLE_ACCEL_MIN, forward_limit / max(0.1, target_time))


def get_vehicle_rotation_speed(vehicle: dict) -> float:
    rot = float((vehicle or {}).get('rotationSpeedLimit') or 0.0)
    if rot <= 0.0:
        rot = float((vehicle or {}).get('chassisRotationSpeed') or 0.0)
        if rot <= 0.0:
            return BATTLE_DEFAULT_ROTATION_SPEED
        cap = float((vehicle or {}).get('chassisRotationSpeedLimit') or 0.0)
        if cap > 0.0:
            rot = min(rot, cap)
    return rot * get_vehicle_rotation_speed_factor(vehicle)


def get_vehicle_rotation_boost_scale(vehicle: dict) -> float:
    weight_t = float((vehicle or {}).get('totalWeightKg') or 0.0) / 1000.0
    if weight_t <= 0.0:
        return 1.0
    if weight_t <= BATTLE_ROTATION_BOOST_FULL_WEIGHT_T:
        return 1.0
    if weight_t >= BATTLE_ROTATION_BOOST_NONE_WEIGHT_T:
        return 0.0
    span = BATTLE_ROTATION_BOOST_NONE_WEIGHT_T - BATTLE_ROTATION_BOOST_FULL_WEIGHT_T
    return (BATTLE_ROTATION_BOOST_NONE_WEIGHT_T - weight_t) / span


def get_vehicle_rotation_speed_factor(vehicle: dict) -> float:
    scale = get_vehicle_rotation_boost_scale(vehicle)
    return 1.0 + (BATTLE_ROTATION_SPEED_FACTOR - 1.0) * scale


def get_vehicle_rotation_accel_factor(vehicle: dict) -> float:
    scale = get_vehicle_rotation_boost_scale(vehicle)
    return 1.0 + (BATTLE_ROTATION_ACCEL_FACTOR_XML - 1.0) * scale


def get_vehicle_min_turn_factor(vehicle: dict) -> float:
    scale = get_vehicle_rotation_boost_scale(vehicle)
    return BATTLE_HEAVY_MIN_TURN_FACTOR + (BATTLE_MIN_TURN_FACTOR - BATTLE_HEAVY_MIN_TURN_FACTOR) * scale


def get_vehicle_motion_rates(vehicle: dict):
    resistance = (vehicle or {}).get('terrainResistance') or [1.0, 1.2, 1.4]
    terrain_resistance = max(0.001, float(resistance[0] if resistance else 1.0))
    engine_power = float((vehicle or {}).get('enginePower') or 0.0)
    weight_t = max(0.001, float((vehicle or {}).get('totalWeightKg') or 0.0) / 1000.0)
    hp_per_ton = engine_power / weight_t if engine_power > 0.0 else float((vehicle or {}).get('hpPerTon') or 0.0)
    if hp_per_ton <= 0.0:
        accel = BATTLE_DEFAULT_ACCEL
    else:
        accel = hp_per_ton * BATTLE_ENGINE_ACCEL_FACTOR / terrain_resistance
    accel = max(accel, get_vehicle_accel_floor(vehicle))
    accel = max(BATTLE_ACCEL_MIN,
                min(BATTLE_ACCEL_MAX, accel))
    brake_force = float((vehicle or {}).get('brakeForce') or 0.0)
    decel = brake_force * BATTLE_BRAKE_FORCE_FACTOR if brake_force > 0.0 else accel * BATTLE_SPEED_DECEL_RATIO
    decel += terrain_resistance * BATTLE_ROLLING_RESISTANCE_FACTOR
    decel = max(BATTLE_DECEL_MIN, min(BATTLE_DECEL_MAX, decel))
    return accel, decel


def get_vehicle_rotation_rates(vehicle: dict):
    chassis_rot = get_vehicle_rotation_speed(vehicle)
    if chassis_rot <= 0.0:
        return BATTLE_DEFAULT_ROT_ACCEL, BATTLE_DEFAULT_ROT_DECEL
    engine_power = float((vehicle or {}).get('enginePower') or 0.0)
    rotation_energy = float((vehicle or {}).get('rotationEnergy') or 0.0)
    accel_factor = get_vehicle_rotation_accel_factor(vehicle)
    if engine_power > 0.0 and rotation_energy > 0.0:
        accel = engine_power / rotation_energy * accel_factor
    else:
        accel = chassis_rot * BATTLE_ROT_ACCEL_FACTOR * accel_factor
    accel_cap = chassis_rot * 3.0
    accel = max(BATTLE_ROT_ACCEL_MIN, min(accel_cap, accel))
    weight_t = float((vehicle or {}).get('totalWeightKg') or 0.0) / 1000.0
    hp_per_ton = float((vehicle or {}).get('hpPerTon') or 0.0)
    if 0.0 < weight_t <= BATTLE_LIGHT_ROT_ACCEL_WEIGHT_T:
        weight_factor = (BATTLE_LIGHT_ROT_ACCEL_WEIGHT_T - weight_t) / BATTLE_LIGHT_ROT_ACCEL_WEIGHT_T
        power_factor = max(0.0, min(1.0, (hp_per_ton - BATTLE_LIGHT_ROT_ACCEL_HP_PER_TON) / BATTLE_LIGHT_ROT_ACCEL_HP_PER_TON))
        accel *= 1.0 + (BATTLE_LIGHT_ROT_ACCEL_BONUS - 1.0) * max(weight_factor, power_factor)
    accel = min(accel_cap, accel)
    decel = max(BATTLE_ROT_DECEL_MIN, accel * BATTLE_ROT_DECEL_RATIO)
    return accel, decel


def format_battle_motion_params(vehicle: dict) -> str:
    """One-line summary of the XML-derived motion params used by server/client
    physics for the given vehicle dict. Used at battle start for diagnostics."""
    if not vehicle:
        return '<no vehicle>'
    name = vehicle.get('name', '?')
    fwd, bwd = get_vehicle_speed_limits(vehicle)
    rot = get_vehicle_rotation_speed(vehicle)
    accel, decel = get_vehicle_motion_rates(vehicle)
    rot_accel, rot_decel = get_vehicle_rotation_rates(vehicle)
    hp = float(vehicle.get('enginePower') or 0.0)
    weight_t = float(vehicle.get('totalWeightKg') or 0.0) / 1000.0
    hp_per_ton = float(vehicle.get('hpPerTon') or 0.0)
    resistance = vehicle.get('terrainResistance') or [0.0, 0.0, 0.0]
    brake = float(vehicle.get('brakeForce') or 0.0)
    min_normal_y = float(vehicle.get('chassisMinPlaneNormalY') or 0.0)
    return (
        f"{name}: fwd={fwd:.2f}m/s ({fwd * 3.6:.1f}km/h), "
        f"rev={bwd:.2f}m/s ({bwd * 3.6:.1f}km/h), "
        f"rot={math.degrees(rot):.1f}deg/s, "
        f"accel={accel:.2f}m/s2 decel={decel:.2f}m/s2, "
        f"rotAccel={math.degrees(rot_accel):.1f}deg/s2 "
        f"rotDecel={math.degrees(rot_decel):.1f}deg/s2, "
        f"engine={hp:.0f}hp, weight={weight_t:.1f}t, hp/t={hp_per_ton:.2f}, "
        f"terrain={','.join(f'{float(r):.2f}' for r in resistance[:3])}, "
        f"brake={brake:.1f}, minNormalY={min_normal_y:.3f}"
    )


def get_vehicle_shell_count(vehicle: dict, compact: int) -> int:
    ammo = list((vehicle or {}).get('defaultAmmo') or [])
    for i in range(0, len(ammo), 2):
        if int(ammo[i]) == int(compact):
            return int(ammo[i + 1])
    return 0


def build_vehicle_ammo_stock(vehicle: dict):
    stock = {}
    ammo = list((vehicle or {}).get('defaultAmmo') or [])
    for i in range(0, len(ammo), 2):
        stock[int(ammo[i])] = max(0, int(ammo[i + 1]))
    return stock


def select_available_shell(stock: dict, preferred: int = 0):
    if preferred and int(stock.get(int(preferred), 0)) > 0:
        return int(preferred)
    for compact, quantity in stock.items():
        if int(quantity) > 0:
            return int(compact)
    return 0


def get_vehicle_shell(vehicle: dict, compact: int = None):
    shells = list((vehicle or {}).get('shells') or [])
    if compact is not None:
        for shell in shells:
            if int(shell.get('compactDescr', 0)) == int(compact):
                return shell
    return shells[0] if shells else {}


def get_current_vehicle_shell(sess: dict):
    vehicle = get_session_battle_vehicle(sess)
    shell_cd = int(sess.get('battle_current_shell') or 0)
    stock = sess.get('battle_ammo_stock') or build_vehicle_ammo_stock(vehicle)
    shell_cd = select_available_shell(stock, shell_cd)
    return get_vehicle_shell(vehicle, shell_cd)


def get_shell_damage(shell: dict) -> int:
    damage = (shell or {}).get('damage') or ()
    if damage:
        base = safe_float(damage[0], 0.0, 0.0)
    else:
        match = re.search(r'(\d+)mm', str((shell or {}).get('name', '')))
        caliber = int(match.group(1)) if match else 75
        base = max(20.0, caliber * 1.6)
    if base <= 0.0:
        base = max(20.0, get_shell_caliber(shell) * 1.6)
    randomization = SHOT_DAMAGE_RANDOMIZATION
    return max(1, int(round(base * random.uniform(1.0 - randomization,
                                                  1.0 + randomization))))


def get_shell_kind(shell: dict) -> str:
    kind = str((shell or {}).get('kind') or '').upper()
    aliases = {
        'AP': 'ARMOR_PIERCING',
        'AP_CR': 'ARMOR_PIERCING_CR',
        'APCR': 'ARMOR_PIERCING_CR',
        'AP_HE': 'ARMOR_PIERCING_HE',
        'HC': 'HOLLOW_CHARGE',
        'HE': 'HIGH_EXPLOSIVE',
    }
    return aliases.get(kind, kind)


def is_armor_piercing_shell(shell: dict) -> bool:
    return get_shell_kind(shell).startswith('ARMOR_PIERCING')


def is_high_explosive_shell(shell: dict) -> bool:
    return get_shell_kind(shell) == 'HIGH_EXPLOSIVE'


def is_hollow_charge_shell(shell: dict) -> bool:
    return get_shell_kind(shell) == 'HOLLOW_CHARGE'


def get_shell_caliber(shell: dict) -> float:
    caliber = safe_float((shell or {}).get('caliber'), 0.0, 0.0)
    if caliber > 0.0:
        return caliber
    match = re.search(r'(\d+)mm', str((shell or {}).get('name', '')))
    return float(int(match.group(1)) if match else 75)


def get_shell_base_penetration(shell: dict, distance: float) -> float:
    power = list((shell or {}).get('piercingPower') or [])
    if len(power) >= 2 and safe_float(power[0], 0.0, 0.0) > 0.0:
        near = safe_float(power[0], 0.0, 0.0)
        far = safe_float(power[1], near, 0.0)
    else:
        near = max(20.0, get_shell_caliber(shell) * 1.6)
        far = near * 0.8
    distance = safe_float(distance, 0.0, 0.0)
    if distance <= SHOT_PENETRATION_NEAR_DISTANCE:
        return near
    if distance >= SHOT_PENETRATION_FAR_DISTANCE:
        return far
    span = SHOT_PENETRATION_FAR_DISTANCE - SHOT_PENETRATION_NEAR_DISTANCE
    k = (distance - SHOT_PENETRATION_NEAR_DISTANCE) / span
    return near + (far - near) * k


def get_randomized_penetration(shell: dict, distance: float) -> float:
    base = get_shell_base_penetration(shell, distance)
    randomization = safe_float((shell or {}).get('piercingPowerRandomization', 0.25),
                               0.25, 0.0, 0.95)
    return base * random.uniform(1.0 - randomization, 1.0 + randomization) * SHOT_PENETRATION_FACTOR


def vehicle_armor_model(vehicle: dict) -> dict:
    model = safe_dict((vehicle or {}).get('armorModel'))
    if model:
        return model
    return {
        'hull': {'primaryArmor': [50.0, 40.0, 35.0], 'armorHomogenization': 1.0},
        'turret': {'primaryArmor': [60.0, 45.0, 40.0], 'armorHomogenization': 1.0},
        'dimensions': {},
    }


def armor_dimensions(model: dict) -> dict:
    dims = safe_dict((model or {}).get('dimensions'))
    half_width = safe_float(dims.get('halfWidth'), SHOT_TANK_HALF_WIDTH, 0.25, 20.0)
    half_length = safe_float(dims.get('halfLength'), SHOT_TANK_HALF_LENGTH, 0.25, 30.0)
    min_height = safe_float(dims.get('minHeight'), SHOT_TANK_MIN_HEIGHT, -5.0, 20.0)
    max_height = safe_float(dims.get('maxHeight'), SHOT_TANK_MAX_HEIGHT,
                            min_height + 0.25, 30.0)
    hull_top = safe_float(dims.get('hullTop'), 2.15, min_height + 0.05,
                          max_height - 0.05)
    center_height = safe_float(dims.get('centerHeight'), SHOT_TANK_CENTER_HEIGHT,
                               min_height, max_height)
    return {
        'halfWidth': half_width,
        'halfLength': half_length,
        'minHeight': min_height,
        'hullTop': hull_top,
        'maxHeight': max_height,
        'centerHeight': center_height,
    }


def vehicle_collision_dimensions(vehicle: dict) -> tuple:
    dims = armor_dimensions(vehicle_armor_model(vehicle))
    half_width = max(
        float(TANK_COLLISION_RADIUS),
        float(dims['halfWidth']) * TANK_COLLISION_HALF_WIDTH_SCALE)
    half_length = max(
        half_width,
        float(dims['halfLength']) * TANK_COLLISION_HALF_LENGTH_SCALE)
    return half_width, half_length


def vehicle_mass_tons(vehicle: dict) -> float:
    return max(5.0, float((vehicle or {}).get('totalWeightKg') or 0.0) / 1000.0)


def compute_ram_damage(source_vehicle: dict, target_vehicle: dict,
                       closing_speed: float) -> tuple:
    closing_speed = min(RAM_MAX_CLOSING_SPEED, max(0.0, float(closing_speed or 0.0)))
    if closing_speed < RAM_MIN_CLOSING_SPEED or RAM_DAMAGE_SCALE <= 0.0:
        return 0, 0
    source_mass = vehicle_mass_tons(source_vehicle)
    target_mass = vehicle_mass_tons(target_vehicle)
    energy = closing_speed * closing_speed * RAM_DAMAGE_SCALE
    damage_to_target = int(round(
        energy * source_mass * clamp(source_mass / target_mass, 0.25, 2.5)))
    damage_to_source = int(round(
        energy * target_mass * clamp(target_mass / source_mass, 0.25, 2.5)))
    return max(0, damage_to_target), max(0, damage_to_source)


def _vehicle_axes(yaw: float):
    sin_yaw = math.sin(yaw)
    cos_yaw = math.cos(yaw)
    return (cos_yaw, -sin_yaw), (sin_yaw, cos_yaw)


def vehicle_footprints_overlap(ax: float, az: float, ayaw: float,
                               ahw: float, ahl: float,
                               bx: float, bz: float, byaw: float,
                               bhw: float, bhl: float,
                               margin: float = 0.0) -> bool:
    a_right, a_forward = _vehicle_axes(ayaw)
    b_right, b_forward = _vehicle_axes(byaw)
    dx = bx - ax
    dz = bz - az
    for axis in (a_right, a_forward, b_right, b_forward):
        dist = abs(dx * axis[0] + dz * axis[1])
        a_extent = (
            ahw * abs(a_right[0] * axis[0] + a_right[1] * axis[1]) +
            ahl * abs(a_forward[0] * axis[0] + a_forward[1] * axis[1]))
        b_extent = (
            bhw * abs(b_right[0] * axis[0] + b_right[1] * axis[1]) +
            bhl * abs(b_forward[0] * axis[0] + b_forward[1] * axis[1]))
        if dist >= a_extent + b_extent + margin:
            return False
    return True


def vehicle_footprint_contact_on_path(prev_x: float, prev_z: float,
                                      new_x: float, new_z: float,
                                      source_yaw: float,
                                      source_half_width: float,
                                      source_half_length: float,
                                      other_x: float, other_z: float,
                                      other_yaw: float,
                                      other_half_width: float,
                                      other_half_length: float,
                                      margin: float):
    prev_overlap = vehicle_footprints_overlap(
        prev_x, prev_z, source_yaw, source_half_width, source_half_length,
        other_x, other_z, other_yaw, other_half_width, other_half_length,
        margin)
    if prev_overlap:
        prev_dx = other_x - prev_x
        prev_dz = other_z - prev_z
        new_dx = other_x - new_x
        new_dz = other_z - new_z
        if new_dx * new_dx + new_dz * new_dz <= prev_dx * prev_dx + prev_dz * prev_dz:
            return {
                'x': prev_x,
                'z': prev_z,
                't': 0.0,
                'prev_overlap': True,
            }
        return None
    dx = new_x - prev_x
    dz = new_z - prev_z
    distance = math.sqrt(dx * dx + dz * dz)
    steps = int(min(16, max(1, math.ceil(distance / TANK_COLLISION_SWEEP_STEP))))
    last_t = 0.0
    for step in range(1, steps + 1):
        t = float(step) / float(steps)
        test_x = prev_x + dx * t
        test_z = prev_z + dz * t
        if not vehicle_footprints_overlap(
                test_x, test_z, source_yaw,
                source_half_width, source_half_length,
                other_x, other_z, other_yaw,
                other_half_width, other_half_length,
                margin):
            last_t = t
            continue
        low = last_t
        high = t
        for _i in range(14):
            mid = (low + high) * 0.5
            mid_x = prev_x + dx * mid
            mid_z = prev_z + dz * mid
            if vehicle_footprints_overlap(
                    mid_x, mid_z, source_yaw,
                    source_half_width, source_half_length,
                    other_x, other_z, other_yaw,
                    other_half_width, other_half_length,
                    margin):
                high = mid
            else:
                low = mid
        contact_x = prev_x + dx * high
        contact_z = prev_z + dz * high
        return {
            'x': contact_x,
            'z': contact_z,
            't': high,
            'prev_overlap': False,
        }
    return None


def vehicle_footprint_blocks_motion(prev_x: float, prev_z: float,
                                    new_x: float, new_z: float,
                                    source_yaw: float,
                                    source_half_width: float,
                                    source_half_length: float,
                                    other_x: float, other_z: float,
                                    other_yaw: float,
                                    other_half_width: float,
                                    other_half_length: float) -> bool:
    return vehicle_footprint_contact_on_path(
        prev_x, prev_z, new_x, new_z,
        source_yaw, source_half_width, source_half_length,
        other_x, other_z, other_yaw,
        other_half_width, other_half_length,
        TANK_COLLISION_MARGIN) is not None


def vehicle_hit_yaw(target_sess: dict) -> float:
    if is_recent_client_vehicle_position(target_sess):
        return float(target_sess.get('client_vehicle_yaw',
                                     target_sess.get('battle_yaw', 0.0)))
    return float(target_sess.get('battle_yaw', 0.0))


def world_to_vehicle_local(point, pos, yaw: float):
    point = safe_vec3(point)
    pos = safe_vec3(pos)
    yaw = safe_float(yaw, 0.0)
    dx = float(point[0]) - float(pos[0])
    dy = float(point[1]) - float(pos[1])
    dz = float(point[2]) - float(pos[2])
    sin_yaw = math.sin(yaw)
    cos_yaw = math.cos(yaw)
    return (
        dx * cos_yaw - dz * sin_yaw,
        dy,
        dx * sin_yaw + dz * cos_yaw,
    )


def local_to_world_vector(vec, yaw: float):
    vec = safe_vec3(vec, (0.0, 0.0, 1.0))
    yaw = safe_float(yaw, 0.0)
    sin_yaw = math.sin(yaw)
    cos_yaw = math.cos(yaw)
    return (
        vec[0] * cos_yaw + vec[2] * sin_yaw,
        vec[1],
        -vec[0] * sin_yaw + vec[2] * cos_yaw,
    )


def ray_vehicle_box_hit(shot_pos, shot_vec, vehicle_pos, yaw: float, dims: dict):
    shot_pos = safe_vec3(shot_pos, None)
    shot_vec = safe_vec3(shot_vec, None)
    vehicle_pos = safe_vec3(vehicle_pos, None)
    if shot_pos is None or shot_vec is None or vehicle_pos is None:
        return None
    shot_vec = normalize_vec(shot_vec)
    dims = armor_dimensions({'dimensions': dims})
    local_origin = world_to_vehicle_local(shot_pos, vehicle_pos, yaw)
    local_dir = world_to_vehicle_local(
        (shot_pos[0] + shot_vec[0], shot_pos[1] + shot_vec[1], shot_pos[2] + shot_vec[2]),
        vehicle_pos, yaw)
    local_dir = (
        local_dir[0] - local_origin[0],
        local_dir[1] - local_origin[1],
        local_dir[2] - local_origin[2],
    )
    bounds = (
        (-dims['halfWidth'], dims['halfWidth']),
        (dims['minHeight'], dims['maxHeight']),
        (-dims['halfLength'], dims['halfLength']),
    )
    t_min = -1.0e30
    t_max = 1.0e30
    normal = (0.0, 0.0, -1.0)
    for axis in range(3):
        origin = local_origin[axis]
        direction = local_dir[axis]
        low, high = bounds[axis]
        if abs(direction) < 1.0e-6:
            if origin < low or origin > high:
                return None
            continue
        t1 = (low - origin) / direction
        t2 = (high - origin) / direction
        n1 = [0.0, 0.0, 0.0]
        n2 = [0.0, 0.0, 0.0]
        n1[axis] = -1.0
        n2[axis] = 1.0
        if t1 > t2:
            t1, t2 = t2, t1
            n1, n2 = n2, n1
        if t1 > t_min:
            t_min = t1
            normal = tuple(n1)
        t_max = min(t_max, t2)
        if t_min > t_max:
            return None
    distance = t_min if t_min >= 0.0 else t_max
    if distance < 0.0 or distance > SHOT_TRACE_DISTANCE:
        return None
    hit_local = (
        local_origin[0] + local_dir[0] * distance,
        local_origin[1] + local_dir[1] * distance,
        local_origin[2] + local_dir[2] * distance,
    )
    hit_world = (
        float(shot_pos[0]) + float(shot_vec[0]) * distance,
        float(shot_pos[1]) + float(shot_vec[1]) * distance,
        float(shot_pos[2]) + float(shot_vec[2]) * distance,
    )
    return hit_local, hit_world, normal, distance


def fallback_hit_from_marker(marker_pos, vehicle_pos, yaw: float, dims: dict):
    marker_pos = safe_vec3(marker_pos, None)
    vehicle_pos = safe_vec3(vehicle_pos, None)
    if marker_pos is None or vehicle_pos is None:
        return None
    dims = armor_dimensions({'dimensions': dims})
    local = world_to_vehicle_local(marker_pos, vehicle_pos, yaw)
    face_x = dims['halfWidth'] - abs(local[0])
    face_z = dims['halfLength'] - abs(local[2])
    if face_x < face_z:
        normal = (1.0 if local[0] >= 0.0 else -1.0, 0.0, 0.0)
    else:
        normal = (0.0, 0.0, 1.0 if local[2] >= 0.0 else -1.0)
    clamped = (
        clamp(local[0], -dims['halfWidth'], dims['halfWidth']),
        clamp(local[1], dims['minHeight'], dims['maxHeight']),
        clamp(local[2], -dims['halfLength'], dims['halfLength']),
    )
    return clamped, tuple(float(v) for v in marker_pos), normal


def marker_inside_vehicle_box(marker_pos, vehicle_pos, yaw: float, dims: dict,
                              margin: float = 0.35):
    marker_pos = safe_vec3(marker_pos, None)
    vehicle_pos = safe_vec3(vehicle_pos, None)
    if marker_pos is None or vehicle_pos is None:
        return None
    dims = armor_dimensions({'dimensions': dims})
    local = world_to_vehicle_local(marker_pos, vehicle_pos, yaw)
    margin = safe_float(margin, 0.35, 0.0, 3.0)
    if abs(local[0]) > dims['halfWidth'] + margin:
        return None
    if abs(local[2]) > dims['halfLength'] + margin:
        return None
    if local[1] < dims['minHeight'] - margin or local[1] > dims['maxHeight'] + margin:
        return None
    return (
        clamp(local[0], -dims['halfWidth'], dims['halfWidth']),
        clamp(local[1], dims['minHeight'], dims['maxHeight']),
        clamp(local[2], -dims['halfLength'], dims['halfLength']),
    )


def direct_marker_entry_normal(local_shot_dir):
    local_shot_dir = normalize_vec(safe_vec3(local_shot_dir, (0.0, 0.0, 1.0)))
    if abs(local_shot_dir[0]) > abs(local_shot_dir[2]):
        return (-1.0 if local_shot_dir[0] > 0.0 else 1.0, 0.0, 0.0)
    return (0.0, 0.0, -1.0 if local_shot_dir[2] > 0.0 else 1.0)


def armor_component_and_zone(hit_local, normal, dims: dict):
    hit_local = safe_vec3(hit_local, (0.0, SHOT_TANK_CENTER_HEIGHT, 0.0))
    normal = safe_vec3(normal, (0.0, 0.0, 1.0))
    dims = armor_dimensions({'dimensions': dims})
    component = 'turret' if hit_local[1] >= dims['hullTop'] else 'hull'
    if abs(normal[2]) >= abs(normal[0]):
        zone = 'front' if normal[2] > 0.0 else 'rear'
    else:
        zone = 'side'
    if component == 'hull' and zone == 'front' and hit_local[1] < dims['hullTop'] * 0.45:
        zone = 'lower_front'
    return component, zone


def get_zone_armor(armor_model: dict, component: str, zone: str) -> float:
    comp = safe_dict((armor_model or {}).get(component))
    primary = list(comp.get('primaryArmor') or [])
    while len(primary) < 3:
        primary.append(50.0)
    if zone == 'front':
        return safe_float(primary[0], 50.0, 1.0, 1000.0)
    if zone == 'lower_front':
        return safe_float(primary[0], 50.0, 1.0, 1000.0) * 0.85
    if zone == 'side':
        return safe_float(primary[1], safe_float(primary[0], 40.0), 1.0, 1000.0)
    return safe_float(primary[2], safe_float(primary[1], 35.0), 1.0, 1000.0)


def get_component_homogenization(armor_model: dict, component: str) -> float:
    comp = safe_dict((armor_model or {}).get(component))
    return safe_float(comp.get('armorHomogenization'), 1.0, 0.1, 10.0)


def impact_cosine(shot_vec, normal, yaw: float) -> float:
    shot_vec = normalize_vec(safe_vec3(shot_vec, (0.0, 0.0, 1.0)))
    world_normal = local_to_world_vector(normal, yaw)
    return max(0.0, -(
        float(shot_vec[0]) * world_normal[0] +
        float(shot_vec[1]) * world_normal[1] +
        float(shot_vec[2]) * world_normal[2]))


def resolve_shot_armor(shell: dict, hit_info: dict) -> dict:
    hit_info = safe_dict(hit_info)
    if hit_info.get('artillerySplash'):
        radius = safe_float(hit_info.get('splashRadius'), 0.0, 0.0)
        distance = safe_float(hit_info.get('splashDistance'), 0.0, 0.0)
        out = dict(hit_info)
        out.update({
            'result': SHOT_RESULT_CRITICAL_HIT,
            'damage': artillery_splash_damage(shell, distance, radius),
            'penetration': 0.0,
            'effectiveArmor': 0.0,
            'impactAngleDeg': 0.0,
        })
        return out
    distance = safe_float(hit_info.get('distance'), 0.0, 0.0)
    armor = safe_float(hit_info.get('armor'), 0.0, 0.0, 1000.0)
    cos_value = safe_float(hit_info.get('impactCos'), 0.0, 0.0, 1.0)
    normalized_cos = cos_value
    if is_armor_piercing_shell(shell):
        angle = math.acos(max(-1.0, min(1.0, cos_value)))
        angle = max(0.0, angle - safe_float((shell or {}).get('normalizationAngle'),
                                             0.0, 0.0, math.pi / 2.0))
        normalized_cos = math.cos(angle)
    effective = armor / max(SHOT_ARMOR_MIN_COS, normalized_cos)
    ricochet_cos = safe_float((shell or {}).get('ricochetAngleCos'),
                              SHOT_ARMOR_AUTORICOCHET_COS, 0.0, 1.0)
    can_ricochet = is_armor_piercing_shell(shell) or is_hollow_charge_shell(shell)
    if can_ricochet and cos_value <= ricochet_cos:
        result = SHOT_RESULT_RICOCHET
        damage = int(round(get_shell_damage(shell) * SHOT_RICOCHET_DAMAGE_FACTOR))
        penetration = get_shell_base_penetration(shell, distance)
    else:
        penetration = get_randomized_penetration(shell, distance)
        if penetration + 1.0e-6 >= effective:
            result = SHOT_RESULT_ARMOR_PIERCED
            damage = get_shell_damage(shell)
        elif is_high_explosive_shell(shell):
            if hit_info.get('artilleryDirectHit'):
                result = SHOT_RESULT_CRITICAL_HIT
                damage = artillery_direct_he_damage(shell)
            else:
                result = SHOT_RESULT_ARMOR_NOT_PIERCED
                damage = max(SHOT_HE_MIN_SPLASH_DAMAGE,
                             int(round(get_shell_damage(shell) * SHOT_HE_NONPEN_DAMAGE_FACTOR)))
        else:
            result = SHOT_RESULT_ARMOR_NOT_PIERCED
            damage = int(round(get_shell_damage(shell) * SHOT_AP_NONPEN_DAMAGE_FACTOR))
    out = dict(hit_info)
    out.update({
        'result': result,
        'damage': damage,
        'penetration': penetration,
        'effectiveArmor': effective,
        'impactAngleDeg': math.degrees(math.acos(max(-1.0, min(1.0, cos_value)))),
    })
    return out


def pack_damage_segment(hit_info: dict) -> int:
    hit_info = safe_dict(hit_info)
    component = hit_info.get('component') or 'hull'
    comp_idx = {'chassis': 0, 'hull': 1, 'turret': 2, 'gun': 3}.get(component, 1)
    result = int(safe_float(hit_info.get('result'),
                            SHOT_RESULT_ARMOR_NOT_PIERCED, 0.0, 255.0)) & 0xff
    local = safe_vec3(hit_info.get('hitLocal'), (0.0, SHOT_TANK_CENTER_HEIGHT, 0.0))
    dims = armor_dimensions({'dimensions': hit_info.get('dimensions') or {}})
    if component == 'turret':
        bounds = (
            (-dims['halfWidth'] * 0.6, dims['halfWidth'] * 0.6),
            (dims['hullTop'], dims['maxHeight']),
            (-dims['halfLength'] * 0.45, dims['halfLength'] * 0.45),
        )
    else:
        bounds = (
            (-dims['halfWidth'], dims['halfWidth']),
            (dims['minHeight'], dims['hullTop']),
            (-dims['halfLength'], dims['halfLength']),
        )
    direction = normalize_vec(safe_vec3(hit_info.get('localShotDir'),
                                        (0.0, 0.0, 1.0)))
    start = (
        local[0] - direction[0] * 0.4,
        local[1] - direction[1] * 0.4,
        local[2] - direction[2] * 0.4,
    )
    end = (
        local[0] + direction[0] * 0.4,
        local[1] + direction[1] * 0.4,
        local[2] + direction[2] * 0.4,
    )

    def enc(value, low, high):
        if high <= low:
            return 0
        value = safe_float(value, low, low, high)
        return int(round(clamp((value - low) / (high - low), 0.0, 1.0) * 255.0)) & 0xff

    sx = enc(start[0], *bounds[0]); sy = enc(start[1], *bounds[1]); sz = enc(start[2], *bounds[2])
    ex = enc(end[0], *bounds[0]); ey = enc(end[1], *bounds[1]); ez = enc(end[2], *bounds[2])
    return (result | (comp_idx << 8) | (sx << 16) | (sy << 24) |
            (sz << 32) | (ex << 40) | (ey << 48) | (ez << 56))


def ballistic_shot_vec(shot_pos, target_pos, speed: float, gravity: float,
                       high_arc: bool = False):
    dx = float(target_pos[0]) - float(shot_pos[0])
    dy = float(target_pos[1]) - float(shot_pos[1])
    dz = float(target_pos[2]) - float(shot_pos[2])
    horizontal = math.sqrt(dx * dx + dz * dz)
    if horizontal <= 0.001 or speed <= 0.001 or gravity <= 0.001:
        return normalize_vec((dx, dy, dz))
    v2 = speed * speed
    g = gravity
    disc = v2 * v2 - g * (g * horizontal * horizontal + 2.0 * dy * v2)
    if disc < 0.0:
        return normalize_vec((dx, dy, dz))
    root = math.sqrt(disc)
    tan_theta = (v2 + root if high_arc else v2 - root) / (g * horizontal)
    cos_theta = 1.0 / math.sqrt(1.0 + tan_theta * tan_theta)
    sin_theta = tan_theta * cos_theta
    return normalize_vec((
        dx / horizontal * cos_theta,
        sin_theta,
        dz / horizontal * cos_theta,
    ))


def make_full_sync_pickle(account_id: int = 0) -> bytes:
    """РџРѕРІРЅРёР№ (full-sync) diff РґР»СЏ Account._update. Р‘РµР· 'prevRev' в†’
    isFullSync=True в†’ РєР»С–С”РЅС‚ С‡РёСЃС‚РёС‚СЊ __cache С– Р·Р°СЃС‚РѕСЃРѕРІСѓС” РЅР°С€ diff.

    inventory[itemTypeIdx] РњРЈРЎРР¤Р¬ РјС–СЃС‚РёС‚Рё sub-dicts, С–РЅР°РєС€Рµ
    InventoryParser.parseVehicles РєСЂР°С€РёС‚СЊ Р· 'NoneType is unsubscriptable'
    РЅР° data['compDescr'] / data['crew'] etc.
    ITEM_TYPE_INDICES (Р· res/scripts/common/items/__init__.py):
      1=vehicle, 2=vehicleChassis, 3=vehicleTurret, 4=vehicleGun,
      5=vehicleEngine, 6=vehicleFuelTank, 7=vehicleRadio, 8=tankman,
      9=optionalDevice, 10=shell, 11=equipment.
    """
    veh_list = load_all_vehicles()
    server_stats = get_server_stats()
    if account_id:
        try:
            acc = load_account_state(int(account_id))
        except Exception as exc:
            print(f"[!] load_account_state({account_id}) failed: {exc}")
            acc = None
    else:
        acc = None
    if acc is None:
        acc = {
            'credits': DEFAULT_CREDITS, 'gold': DEFAULT_GOLD,
            'free_xp': DEFAULT_FREE_XP, 'slots': DEFAULT_SLOTS,
            'berths': DEFAULT_BERTHS, 'premium_expire': 0, 'attrs': 0,
            'clan_db_id': 0, 'unlocks': set(),
            'elite_vehicles': set(), 'double_xp_vehs': set(),
        }

    # InventoryParser.parseVehicles С‡РёС‚Р°С” С‚Р°РєС– sub-dicts:
    #   compDescr[id]    в†’ vehCompDescr (raw bytes)
    #   shellsLayout[id] в†’ dict
    #   shells[id]       в†’ list
    #   crew[id]         в†’ list (tankman invIDs)
    #   repair[id]       в†’ tuple (repairCost, health)
    #   eqsLayout[id]    в†’ list[3]
    #   eqs[id]          в†’ list[3]
    #   settings[id]     в†’ int
    #   lock[id]         в†’ int
    veh_compDescr = {}
    veh_shellsLayout = {}
    veh_shells = {}
    veh_crew = {}
    veh_repair = {}
    veh_eqsLayout = {}
    veh_eqs = {}
    veh_settings = {}
    veh_lock = {}
    if account_id:
        tankmen_rows = load_account_tankmen(account_id)
        consumables  = load_account_consumables(account_id)
        opt_devices  = load_account_optional_devices(account_id)
        veh_eq_slots = load_vehicle_consumable_slots(account_id)
        veh_od_slots = load_vehicle_optional_device_slots(account_id)
        veh_ammo_layouts = load_vehicle_ammo_layouts(account_id)
    else:
        tankmen_rows, consumables, opt_devices = [], {}, {}
        veh_eq_slots, veh_od_slots = {}, {}
        veh_ammo_layouts = {}

    veh_by_inv = get_vehicle_inventory_map()
    tankmen_compDescr = {}
    tankmen_vehicle   = {}
    crew_by_vehicle   = {}
    crew_slots_by_vehicle = {}
    for tm in tankmen_rows:
        tm_inv = int(tm['inv_id'])
        veh_inv = int(tm['vehicle_inv_id'] or 0)
        nation_id = int(tm['nation_id'])
        vehicle_type_id = int(tm['vehicle_type_id'])
        first_name_id = int(tm['first_name_id'] or 0)
        last_name_id  = int(tm['last_name_id'] or 0)
        icon_id       = int(tm['icon_id'] or 0)
        is_female     = bool(tm.get('is_female'))
        if veh_inv:
            assigned = veh_by_inv.get(veh_inv)
            if assigned is not None:
                veh_nation = int(assigned.get('nationID') or 0)
                veh_vtype  = int(assigned.get('vehicleTypeID') or 0)
                if nation_id != veh_nation or vehicle_type_id != veh_vtype:
                    nation_id = veh_nation
                    vehicle_type_id = veh_vtype
                    nation_name = assigned.get('nation') or 'ussr'
                    passport = pick_random_passport(
                        nation_name, is_premium=bool(tm.get('is_premium')))
                    if passport is not None:
                        first_name_id = int(passport['first_name_id'])
                        last_name_id  = int(passport['last_name_id'])
                        icon_id       = int(passport['icon_id'])
                        is_female     = bool(passport['is_female'])
        cd_bytes = make_tankman_compact_descr(
            nation_id, vehicle_type_id,
            tm['role_id'], tm['role_level'],
            first_name_id, last_name_id, icon_id,
            is_female=is_female, is_premium=tm['is_premium'],
            free_xp=tm['free_xp'], skills=tm['skills'],
            last_skill_level=tm['last_skill_level'])
        tankmen_compDescr[tm_inv] = cd_bytes
        if veh_inv:
            tankmen_vehicle[tm_inv] = veh_inv
            slot_idx = tm.get('slot_idx')
            if slot_idx is not None and int(slot_idx) >= 0:
                crew_slots_by_vehicle.setdefault(veh_inv, {})[
                    int(slot_idx)] = tm_inv
            else:
                crew_by_vehicle.setdefault(veh_inv, []).append(tm_inv)

    FAKE_TANKMAN_INVID = 0
    shell_inventory = {}
    for vehicle in veh_list:
        inv_id = vehicle['inv_id']
        crew_size = vehicle['crewSize']
        opt_device_slots = list(veh_od_slots.get(inv_id, [0, 0, 0]))
        cd = make_vehicle_compact_descr_with_optional_devices(
            vehicle['compactDescr'], opt_device_slots)
        default_ammo = list(veh_ammo_layouts.get(
            inv_id, vehicle.get('defaultAmmo') or []))
        shells_layout = {}
        turret_cd = vehicle.get('turretCompactDescr', 0)
        gun_cd = vehicle.get('gunCompactDescr', 0)
        if turret_cd and gun_cd and default_ammo:
            shells_layout[(turret_cd, gun_cd)] = list(default_ammo)

        veh_compDescr[inv_id] = cd
        veh_shellsLayout[inv_id] = shells_layout
        veh_shells[inv_id] = list(default_ammo)

        crew_for_veh = [FAKE_TANKMAN_INVID] * crew_size
        used_tankmen = set()
        for slot_idx, tm_inv in crew_slots_by_vehicle.get(inv_id, {}).items():
            if 0 <= int(slot_idx) < crew_size:
                crew_for_veh[int(slot_idx)] = tm_inv
                used_tankmen.add(tm_inv)
        free_slots = [
            idx for idx, tm_inv in enumerate(crew_for_veh)
            if tm_inv == FAKE_TANKMAN_INVID
        ]
        for tm_inv in crew_by_vehicle.get(inv_id, []):
            if not free_slots or tm_inv in used_tankmen:
                continue
            crew_for_veh[free_slots.pop(0)] = tm_inv
        veh_crew[inv_id] = crew_for_veh

        veh_repair[inv_id] = (0, 1.0)
        veh_eqsLayout[inv_id] = opt_device_slots
        veh_eqs[inv_id]       = list(veh_eq_slots.get(inv_id, [0, 0, 0]))
        veh_settings[inv_id] = 0
        veh_lock[inv_id] = 0
        for i in range(0, len(default_ammo), 2):
            compact = default_ammo[i]
            count = default_ammo[i + 1]
            shell_inventory[compact] = shell_inventory.get(compact, 0) + count

    print(f"[*] Inventory: {len(veh_list)} vehicles Р·Р°РІР°РЅС‚Р°Р¶РµРЅРѕ")
    slots_count = max(200, len(veh_list) + 10)

    diff = {
        'rev': get_account_sync_revision(account_id),
        'inventory': {
            1: {  # vehicle
                'compDescr': veh_compDescr,
                'shellsLayout': veh_shellsLayout,
                'shells': veh_shells,
                'crew': veh_crew,
                'repair': veh_repair,
                'eqsLayout': veh_eqsLayout,
                'eqs': veh_eqs,
                'settings': veh_settings,
                'lock': veh_lock,
            },
            2: {}, 3: {}, 4: {}, 5: {}, 6: {}, 7: {},
            8: {'compDescr': dict(tankmen_compDescr),
                'vehicle':   dict(tankmen_vehicle)},
            9: dict(opt_devices),
            10: shell_inventory,
            11: dict(consumables),
        },
        # Stats._CACHE_STATS = ('battlesTillCaptcha',) в†’ СЃР°РјРµ Р·РІС–РґСЃРё
        # CaptchaUI С‡РёС‚Р°С” battlesTillCaptcha. РЇРєС‰Рѕ РЅРµ РїРѕРєР»Р°СЃС‚Рё, Stats.get
        # РїРѕРІРµСЂС‚Р°С” None, _battlesTillCaptcha=None в†’ `None <= 0` == True Сѓ
        # Python 2 в†’ isCaptchaRequired=True в†’ CAPTCHA Р·Р°РїСѓСЃРєР°С”С‚СЊСЃСЏ.
        'cache': {'vehsLock': {}, 'battlesTillCaptcha': 999},
        # Stats.synchronize в†’ diff['stats']:
        #   _SIMPLE_VALUE_STATS: credits, gold, slots, berths, freeXP, dossier,
        #     clanInfo, accOnline, accOffline, freeTMenLeft, freeVehiclesLeft,
        #     captchaTriesLeft, hasFinPassword, tkillIsSuspected
        #   _GROWING_SET_STATS: unlocks, eliteVehicles, doubleXPVehs (set update)
        # РЇРєС‰Рѕ РЅРµ РґР°С‚Рё 'slots'/'doubleXPVehs' вЂ” Stats.get('slots') РїРѕРІРµСЂС‚Р°С” None
        # в†’ Hangar.__updateVehicles РїР°РґР°С” Р· 'NoneType is unsubscriptable'.
        'stats': {
            'credits': int(acc['credits']),
            'gold': int(acc['gold']),
            'freeXP': int(acc['free_xp']),
            'slots': max(slots_count, int(acc['slots'])),
            'berths': int(acc['berths']),
            'vehTypeXP': {}, 'tankmen': {},
            'unlocks': (set(acc['unlocks']) | generate_all_unlock_descriptors()) if UNLOCK_ALL_VEHICLES else set(acc['unlocks']),
            'eliteVehicles': set(acc['elite_vehicles']),
            'doubleXPVehs': set(acc['double_xp_vehs']),
            'dossier': '', 'clanInfo': None,
            'accOnline': server_stats['playersCount'], 'accOffline': 0,
            'freeTMenLeft': 100, 'freeVehiclesLeft': slots_count - len(veh_list),
            'captchaTriesLeft': 5, 'hasFinPassword': False,
            'tkillIsSuspected': False,
        },
        'shop': {'rev': 0},
        # _ACCOUNT_STATS: clanDBID, attrs, premiumExpiryTime, autoBanTime,
        # restrictions -> Stats.__cache.
        'account': {
            'attrs': int(acc['attrs']),
            'premiumExpiryTime': int(acc['premium_expire']),
            'clanDBID': int(acc['clan_db_id']),
            'autoBanTime': 0, 'restrictions': {},
        },
    }
    # protocol=2 (binary) СЃРєРѕСЂРѕС‡СѓС” pickle ~30% РїРѕСЂС–РІРЅСЏРЅРѕ Р· protocol=0 в†’
    # 1655B в†’ 1197B, РїРѕРјС–С‰Р°С”С‚СЊСЃСЏ Сѓ 1472B BigWorld Packet::MAX_SIZE.
    # cPickle.loads РЅР° РєР»С–С”РЅС‚С– Р°РІС‚РѕРјР°С‚РёС‡РЅРѕ РІРёР·РЅР°С‡Р°С” protocol.
    return pickle.dumps(diff, protocol=2)


_CACHED_SYNC_PICKLE = {}
_CACHED_SYNC_BLOB = {}
_CACHED_SHOP_BLOB = None
_ACCOUNT_SYNC_REV = {}
_EXTERNAL_SYNC_REV_CACHE = {}
_sync_cache_lock = threading.RLock()


def get_account_sync_revision(account_id: int = 0) -> int:
    key = int(account_id or 0)
    with _sync_cache_lock:
        return int(_ACCOUNT_SYNC_REV.get(key, 0))


def bump_account_sync_revision(account_id: int = 0) -> int:
    key = int(account_id or 0)
    with _sync_cache_lock:
        rev = int(_ACCOUNT_SYNC_REV.get(key, 0)) + 1
        _ACCOUNT_SYNC_REV[key] = rev
        _CACHED_SYNC_PICKLE.pop(key, None)
        _CACHED_SYNC_BLOB.pop(key, None)
        _EXTERNAL_SYNC_REV_CACHE.pop(key, None)
        return rev


def get_sync_pickle(account_id: int = 0) -> bytes:
    key = int(account_id or 0)
    with _sync_cache_lock:
        cached = _CACHED_SYNC_PICKLE.get(key)
        if cached is None:
            cached = make_full_sync_pickle(key)
            _CACHED_SYNC_PICKLE[key] = cached
        return cached


def get_sync_blob(account_id: int = 0) -> bytes:
    key = int(account_id or 0)
    with _sync_cache_lock:
        cached = _CACHED_SYNC_BLOB.get(key)
        if cached is None:
            cached = zlib.compress(get_sync_pickle(key))
            _CACHED_SYNC_BLOB[key] = cached
        return cached


def invalidate_sync_cache(account_id: int = 0):
    key = int(account_id or 0)
    with _sync_cache_lock:
        _CACHED_SYNC_PICKLE.pop(key, None)
        _CACHED_SYNC_BLOB.pop(key, None)
        _EXTERNAL_SYNC_REV_CACHE.pop(key, None)


def make_empty_sync_pickle(prev_rev=0) -> bytes:
    return pickle.dumps({'rev': prev_rev, 'prevRev': prev_rev}, protocol=2)


def parse_sync_requested_revision(payload: bytes) -> int:
    args = parse_doCmd_int3(payload)
    if args is None:
        return 0
    first, second, _third = args
    return ((int(first) & 0xffffffff) << 32) | (int(second) & 0xffffffff)


def push_account_diff(sock, addr, sess, partial_diff: dict) -> bool:
    if not sock or not addr or not sess or not partial_diff:
        return False
    account_id = int(sess.get('account_id') or 0)
    if not account_id:
        return False
    prev_rev = get_account_sync_revision(account_id)
    new_rev = bump_account_sync_revision(account_id)
    diff = dict(partial_diff)
    diff['prevRev'] = prev_rev
    diff['rev'] = new_rev
    msg = build_account_update(diff)
    return bool(send_avatar_messages(sock, addr, sess, msg,
                                     f"Account.update(rev={new_rev})",
                                     reliable=True))


def build_vehicle_inventory_diff(account_id: int, veh_inv_id: int) -> dict:
    veh_inv_id = int(veh_inv_id)
    vehicle = get_vehicle_by_inventory_id(veh_inv_id)
    if vehicle is None:
        return {}
    veh_eq_slots     = load_vehicle_consumable_slots(account_id)
    veh_od_slots     = load_vehicle_optional_device_slots(account_id)
    veh_ammo_layouts = load_vehicle_ammo_layouts(account_id)
    opt_devs = list(veh_od_slots.get(veh_inv_id, [0, 0, 0]))
    cd = make_vehicle_compact_descr_with_optional_devices(
        vehicle['compactDescr'], opt_devs)
    eqs = list(veh_eq_slots.get(veh_inv_id, [0, 0, 0]))
    default_ammo = list(veh_ammo_layouts.get(
        veh_inv_id, vehicle.get('defaultAmmo') or []))
    turret_cd = vehicle.get('turretCompactDescr', 0)
    gun_cd    = vehicle.get('gunCompactDescr', 0)
    shells_layout = {}
    if turret_cd and gun_cd and default_ammo:
        shells_layout[(turret_cd, gun_cd)] = list(default_ammo)
    return {
        'inventory': {
            1: {
                'compDescr':    {veh_inv_id: cd},
                'eqs':          {veh_inv_id: eqs},
                'eqsLayout':    {veh_inv_id: opt_devs},
                'shells':       {veh_inv_id: list(default_ammo)},
                'shellsLayout': {veh_inv_id: shells_layout},
            },
        },
    }


def build_crew_inventory_diff(account_id: int, veh_inv_id: int,
                              dismissed_tankman: int = 0) -> dict:
    veh_inv_id = int(veh_inv_id)
    veh_by_inv = get_vehicle_inventory_map()
    vehicle = veh_by_inv.get(veh_inv_id)
    crew_size = int(vehicle.get('crewSize') or 4) if vehicle else 0
    tankmen_rows = load_account_tankmen(account_id)
    crew_for_veh = [0] * crew_size
    free_tail = []
    tankmen_compDescr_diff = {}
    tankmen_vehicle_diff = {}
    for tm in tankmen_rows:
        tm_inv = int(tm['inv_id'])
        veh_for_tm = int(tm.get('vehicle_inv_id') or 0)
        nation_id = int(tm['nation_id'])
        vehicle_type_id = int(tm['vehicle_type_id'])
        first_name_id = int(tm['first_name_id'] or 0)
        last_name_id  = int(tm['last_name_id'] or 0)
        icon_id       = int(tm['icon_id'] or 0)
        is_female     = bool(tm.get('is_female'))
        if veh_for_tm:
            assigned = veh_by_inv.get(veh_for_tm)
            if assigned is not None:
                veh_nation = int(assigned.get('nationID') or 0)
                veh_vtype  = int(assigned.get('vehicleTypeID') or 0)
                if nation_id != veh_nation or vehicle_type_id != veh_vtype:
                    nation_id = veh_nation
                    vehicle_type_id = veh_vtype
                    nation_name = assigned.get('nation') or 'ussr'
                    passport = pick_random_passport(
                        nation_name, is_premium=bool(tm.get('is_premium')))
                    if passport is not None:
                        first_name_id = int(passport['first_name_id'])
                        last_name_id  = int(passport['last_name_id'])
                        icon_id       = int(passport['icon_id'])
                        is_female     = bool(passport['is_female'])
        cd_bytes = make_tankman_compact_descr(
            nation_id, vehicle_type_id,
            tm['role_id'], tm['role_level'],
            first_name_id, last_name_id, icon_id,
            is_female=is_female, is_premium=tm['is_premium'],
            free_xp=tm['free_xp'], skills=tm['skills'],
            last_skill_level=tm['last_skill_level'])
        slot_idx   = tm.get('slot_idx')
        if veh_for_tm == veh_inv_id:
            tankmen_compDescr_diff[tm_inv] = cd_bytes
            tankmen_vehicle_diff[tm_inv] = veh_inv_id
            if (slot_idx is not None and 0 <= int(slot_idx) < crew_size):
                crew_for_veh[int(slot_idx)] = tm_inv
            else:
                free_tail.append(tm_inv)
        elif veh_for_tm == 0:
            tankmen_compDescr_diff[tm_inv] = cd_bytes
            tankmen_vehicle_diff[tm_inv] = None
    used = set(x for x in crew_for_veh if x)
    for idx in range(len(crew_for_veh)):
        if crew_for_veh[idx] == 0 and free_tail:
            tm_inv = free_tail.pop(0)
            if tm_inv in used:
                continue
            crew_for_veh[idx] = tm_inv
            used.add(tm_inv)
    if dismissed_tankman:
        tankmen_compDescr_diff[int(dismissed_tankman)] = None
        tankmen_vehicle_diff[int(dismissed_tankman)] = None
    return {
        'inventory': {
            1: {'crew': {veh_inv_id: crew_for_veh}},
            8: {
                'compDescr': tankmen_compDescr_diff,
                'vehicle':   tankmen_vehicle_diff,
            },
        },
    }


def build_account_state_diff(account_id: int) -> dict:
    state = load_account_state(account_id)
    return {
        'stats': {
            'credits': int(state['credits']),
            'gold':    int(state['gold']),
            'freeXP':  int(state['free_xp']),
        },
        'inventory': {
            9:  dict(load_account_optional_devices(account_id)),
            10: {},
            11: dict(load_account_consumables(account_id)),
        },
    }


def build_vehicle_lock_diff(veh_inv_id: int, lock_reason) -> dict:
    return {
        'cache': {
            'vehsLock': {
                int(veh_inv_id): lock_reason,
            },
        },
    }


def push_vehicle_lock_diff(sock, addr, sess, lock_reason,
                           veh_inv_id: int = None) -> bool:
    if veh_inv_id is None:
        veh_inv_id = sess.get('battle_vehicle_inv_id')
    if not veh_inv_id:
        return False
    return push_account_diff(sock, addr, sess,
                             build_vehicle_lock_diff(veh_inv_id,
                                                     lock_reason))


def make_empty_ext_pickle() -> bytes:
    return pickle.dumps({}, protocol=0)


def make_shop_pickle() -> bytes:
    """Shop.__cache повний словник, що буде записаний клієнтом у
    `Shop.__cache = data` після onSyncStreamComplete (zlib + cPickle).
    requesters.py запитує items для всіх nation x itemType пар; якщо
    `__cache['items'][nation][itemType]` = None → ShopParser.parseModules
    впаде з 'NoneType' is unsubscriptable."""
    NATION_IDS  = (0, 1, 2, 15)
    ITEM_TYPES  = tuple(range(1, 12))
    items = {n: {it: ({}, set()) for it in ITEM_TYPES} for n in NATION_IDS}

    def ps(b, o):
        s = []; i = o; c = []
        while i < len(b):
            ch = b[i]; i += 1
            if ch == 0:
                t = bytes(c).decode('latin1')
                if t == '':
                    return s, i
                s.append(t); c = []
            else:
                c.append(ch)

    def parse(blob, typ, name, strings):
        n = {'name': name, 'type': typ, 'children': [], 'data': b''}
        if typ != 0:
            n['data'] = blob; return n
        if len(blob) < 2:
            return n
        nc = struct.unpack_from('<h', blob, 0)[0]; p = 2; rec = []
        for _ in range(max(0, nc)):
            if p + 6 > len(blob):
                break
            dp, k = struct.unpack_from('<ih', blob, p); p += 6; rec.append((dp, k))
        if p + 4 > len(blob):
            return n
        fin = struct.unpack_from('<i', blob, p)[0]; p += 4
        do = p; dps = [r[0] for r in rec] + [fin]
        own = (dps[0] & 0x0fffffff) if rec else (fin & 0x0fffffff)
        n['data'] = blob[do:do + own]
        for i, (dp, k) in enumerate(rec):
            st = dp & 0x0fffffff; en = dps[i + 1] & 0x0fffffff
            ct = (dps[i + 1] & 0xf0000000) >> 28
            cn = strings[k] if 0 <= k < len(strings) else f'__{k}'
            n['children'].append(parse(blob[do + st:do + en], ct, cn, strings))
        return n

    def find(n, name):
        if n is None:
            return None
        for c in n['children']:
            if c['name'] == name:
                return c
        return None

    def get_int(n, default=0):
        if n is None:
            return default
        d = n['data']
        if not d:
            return default
        t = n.get('type', 2)
        if t == 2:
            return int.from_bytes(d, 'little', signed=True)
        if t == 1:
            try:
                return int(float(d.decode('latin1').strip()))
            except Exception:
                return default
        if len(d) <= 8:
            return int.from_bytes(d, 'little', signed=True)
        return default

    def load(fp):
        if not os.path.exists(fp):
            return None
        try:
            with open(fp, 'rb') as f:
                b = f.read()
        except Exception:
            return None
        if not b:
            return None
        s, o = ps(b, 5)
        return parse(b[o:], 0, 'r', s)

    def parse_components_xml(path):
        n = load(path)
        if n is None:
            return {}
        ids_node = find(n, 'ids')
        if ids_node is None:
            return {}
        res = {}
        for c in ids_node['children']:
            res[c['name']] = get_int(c)
        return res

    def parse_shared_prices(xml_path, ids_map, item_type, nation_id):
        prices = {}
        root = load(xml_path)
        if root is None:
            return prices
        shared = find(root, 'shared')
        if shared is None:
            return prices
        for comp_node in shared['children']:
            name = comp_node['name']
            if name in ids_map:
                comp_id = ids_map[name]
                price_node = find(comp_node, 'price')
                if price_node is not None:
                    price_val = get_int(price_node)
                    gold_node = find(price_node, 'gold')
                    price = (0, price_val) if gold_node is not None else (price_val, 0)
                    comp_descr = (comp_id << 8) | (nation_id << 4) | item_type
                    prices[comp_descr] = price
        return prices

    import json
    vehicles_json_path = os.path.join(ROOT_DIR, 'data', '_vehicles.json')
    if os.path.exists(vehicles_json_path):
        with open(vehicles_json_path, 'r', encoding='utf-8') as f:
            veh_data = json.load(f)
        for veh in veh_data.get('vehicles', []):
            nation_name = veh.get('nation')
            nations_map = {'ussr': 0, 'germany': 1, 'usa': 2}
            nation_id = nations_map.get(nation_name)
            if nation_id is not None:
                veh_id = veh.get('vehicleTypeID')
                price_val = veh.get('price', 0)
                tags = veh.get('tags', [])
                is_premium = 'premium' in tags or 'promo' in tags or veh.get('level', 1) in (8, 9, 10) and price_val < 50000
                price = (0, price_val) if is_premium else (price_val, 0)
                items[nation_id][1][0][veh_id] = price
                for shell in veh.get('shells', []):
                    shell_descr = int(shell.get('compactDescr', 0))
                    if shell_descr:
                        shell_nation = (shell_descr >> 4) & 15
                        shell_price = shell.get('price', [0, 0])
                        items[shell_nation][10][0][shell_descr] = tuple(shell_price)

    client_root = r'C:\Users\qwerty\Desktop\World_of_Tanks'
    for r in get_client_roots():
        if os.path.exists(r):
            client_root = r
            break
    base_dir = os.path.join(client_root, 'res', 'scripts', 'item_defs', 'vehicles')
    nations_map = {'ussr': 0, 'germany': 1, 'usa': 2}
    for nation_name, nation_id in nations_map.items():
        comp_dir = os.path.join(base_dir, nation_name, 'components')
        comp_configs = [
            ('guns.xml', 4),
            ('engines.xml', 5),
            ('fuelTanks.xml', 6),
            ('radios.xml', 7),
        ]
        for fname, itype in comp_configs:
            xml_path = os.path.join(comp_dir, fname)
            ids_map = parse_components_xml(xml_path)
            comp_prices = parse_shared_prices(xml_path, ids_map, itype, nation_id)
            items[nation_id][itype][0].update(comp_prices)

        chassis_map = parse_components_xml(os.path.join(comp_dir, 'chassis.xml'))
        turret_map = parse_components_xml(os.path.join(comp_dir, 'turrets.xml'))
        nation_dir = os.path.join(base_dir, nation_name)
        if os.path.exists(nation_dir):
            for file in os.listdir(nation_dir):
                if file.endswith('.xml') and file != 'list.xml':
                    veh_xml_path = os.path.join(nation_dir, file)
                    root = load(veh_xml_path)
                    if root is None:
                        continue
                    chassis_node = find(root, 'chassis')
                    if chassis_node:
                        for ch in chassis_node['children']:
                            name = ch['name']
                            if name in chassis_map:
                                comp_id = chassis_map[name]
                                price_node = find(ch, 'price')
                                if price_node is not None:
                                    price_val = get_int(price_node)
                                    gold_node = find(price_node, 'gold')
                                    price = (0, price_val) if gold_node is not None else (price_val, 0)
                                    comp_descr = (comp_id << 8) | (nation_id << 4) | 2
                                    items[nation_id][2][0][comp_descr] = price

                    for child in root['children']:
                        if child['name'].startswith('turrets'):
                            for tur in child['children']:
                                name = tur['name']
                                if name in turret_map:
                                    comp_id = turret_map[name]
                                    price_node = find(tur, 'price')
                                    if price_node is not None:
                                        price_val = get_int(price_node)
                                        gold_node = find(price_node, 'gold')
                                        price = (0, price_val) if gold_node is not None else (price_val, 0)
                                        comp_descr = (comp_id << 8) | (nation_id << 4) | 3
                                        items[nation_id][3][0][comp_descr] = price

    common_dir = os.path.join(base_dir, 'common')
    for fname, itype in (('optional_devices.xml', 9), ('equipments.xml', 11)):
        xml_path = os.path.join(common_dir, fname)
        if os.path.exists(xml_path):
            root = load(xml_path)
            if root is not None:
                for child in root['children']:
                    if child['name'] in ('xmlns:xmlref', 'shared'):
                        continue
                    idn = find(child, 'id')
                    if idn is None:
                        continue
                    item_id = get_int(idn)
                    price_node = find(child, 'price')
                    price = (0, 0)
                    if price_node is not None:
                        price_val = get_int(price_node)
                        gold_node = find(price_node, 'gold')
                        price = (0, price_val) if gold_node is not None else (price_val, 0)
                    comp_descr = (item_id << 8) | (15 << 4) | itype
                    items[15][itype][0][comp_descr] = price

    shop = {
        'rev': 1,
        'slotsPrices':       (5, [300, 600, 900, 1200, 1500, 1800]),
        'berthsPrices':      (10, 8, [200, 400, 600, 800]),
        'exchangeRate':      400,
        'freeXPConversion':  (200, 25),
        'premiumCost':       {1: 250, 3: 600, 7: 1250, 14: 2500, 30: 4500},
        'tradeFees':         (0.0, 0.0),
        'tankmanCost':       [{'credits': 0,   'gold': 0},
                              {'credits': 500, 'gold': 0},
                              {'credits': 0,   'gold': 200}],
        'paidRemovalCost':   5,
        'camouflageCost':    {0: [], 1: [], 2: []},
        'hornCost':          (5, 0),
        'passportChangeCost': 50,
        'items':             items,
        'sellPriceModif':    0.5,
    }
    return pickle.dumps(shop, protocol=2)


def get_shop_blob() -> bytes:
    """zlib(pickle(shop)) вЂ” СЏРє С‚РѕРіРѕ С‡РµРєР°С” SyncController.__onSyncStreamComplete."""
    global _CACHED_SHOP_BLOB
    if _CACHED_SHOP_BLOB is None:
        _CACHED_SHOP_BLOB = zlib.compress(make_shop_pickle())
    return _CACHED_SHOP_BLOB


def build_resource_header(stream_id: int, desc: bytes = b'') -> bytes:
    """resourceHeader (msgID 0x37): uint16 id + STRING desc."""
    payload = struct.pack('<H', stream_id) + bw_pack_string(desc)
    return msg_varlen(RESOURCE_HEADER_MSG_ID, payload)


def build_resource_fragment(stream_id: int, seq: int, flags: int,
                            data: bytes) -> bytes:
    """resourceFragment (msgID 0x38):
       uint16 rid + uint8 seq + uint8 flags + raw data (rest of packet)."""
    payload = struct.pack('<HBB', stream_id, seq, flags) + data
    return msg_varlen(RESOURCE_FRAGMENT_MSG_ID, payload)


def send_zlib_pickle_stream(sock, addr, sess, req_id: int, blob: bytes,
                            desc: bytes, label: str,
                            ext_pickle: bytes = None):
    if ext_pickle is None:
        ext_pickle = pickle.dumps({}, protocol=0)

    msgs = build_oncmdrespext(req_id, RES_STREAM, ext_pickle)
    msgs += build_resource_header(req_id, desc)
    send_lock = sess.setdefault('send_lock', threading.RLock())
    try:
        with send_lock:
            pkt = build_channel_packet(msgs, sess, reliable=True)
            pkt = bw_encrypt_packet(pkt, sess['bf_key'])
            runtime_sendto(sock, pkt, addr)
    except Exception:
        return

    max_frag = 1200
    chunks = [blob[i:i + max_frag] for i in range(0, len(blob), max_frag)] or [b'']
    for i, chunk in enumerate(chunks):
        flags = 1 if i == len(chunks) - 1 else 0
        msg = build_resource_fragment(req_id, i, flags, chunk)
        try:
            with send_lock:
                pkt = build_channel_packet(msg, sess, reliable=True)
                pkt = bw_encrypt_packet(pkt, sess['bf_key'])
                runtime_sendto(sock, pkt, addr)
        except Exception:
            return

    print(f"    [>] {label}: req={req_id}, blob={len(blob)}B, "
          f"fragments={len(chunks)}")


def send_sync_stream(sock, addr, sess, req_id: int):
    account_id = int(sess.get('account_id') or 0)
    send_zlib_pickle_stream(sock, addr, sess, req_id, get_sync_blob(account_id),
                            b'syncData', 'syncData STREAM')


DOSSIER_TYPE_ACCOUNT = 1
DOSSIER_TYPE_VEHICLE = 2
DOSSIER_TYPE_TANKMAN = 4


def send_dossiers_stream(sock, addr, sess, req_id: int):
    account_id = int(sess.get('account_id') or 0)
    dossiers_list = []
    try:
        tankmen = load_account_tankmen(account_id) if account_id else []
    except Exception:
        tankmen = []
    for tm in tankmen:
        tm_inv = int(tm['inv_id'])
        dossiers_list.extend([DOSSIER_TYPE_TANKMAN, tm_inv, 0, b''])
    blob = zlib.compress(pickle.dumps((1, dossiers_list), protocol=2))
    send_zlib_pickle_stream(sock, addr, sess, req_id, blob,
                            b'dossiers', 'Dossiers STREAM')


def send_shop_stream(sock, addr, sess, req_id: int):
    """Р РµР°Р»С–Р·СѓС” BigWorld stream protocol РґР»СЏ Shop sync:
       1. cmd response Р· resultID=RES_STREAM (1) в†’ РєР»С–С”РЅС‚ РІРёРєР»РёРєР°С”
          `_subscribeForStream(requestID, callback)`.
       2. resourceHeader(streamID=req_id, desc='') вЂ” Р·Р°РґР°С” pDesc_.
       3. resourceFragment(rid=req_id, seq=0, flags=1, data=zlib(pickle(shop)))
          вЂ” flags=1 СЃС‚Р°РІРёС‚СЊ hasLast_=true в†’ DataDownload.complete()=true в†’
          Account.onStreamComplete(req_id, blob) в†’ SyncController.__onSync
          StreamComplete в†’ Shop.__onSyncComplete(syncID, data) в†’ cache=data."""
    ext = pickle.dumps({'shopRev': 1}, protocol=0)
    send_zlib_pickle_stream(sock, addr, sess, req_id, get_shop_blob(),
                            b'shop', 'Shop STREAM', ext)
    return
    blob = get_shop_blob()

    # 1. cmd response RES_STREAM
    ext = pickle.dumps({'shopRev': 1}, protocol=0)
    msgs = build_oncmdrespext(req_id, RES_STREAM, ext)
    # 2. resource header (РѕРїРёСЃ РїРѕСЂРѕР¶РЅС–Р№ вЂ” РєР»С–С”РЅС‚СЃСЊРєРёР№ onStreamComplete РЅРµ
    # РІРёРєРѕСЂРёСЃС‚РѕРІСѓС” desc РґР»СЏ Shop sync, Р°Р»Рµ pDesc_ != NULL РїРѕС‚СЂС–Р±РµРЅ РґР»СЏ
    # complete()=true).
    msgs += build_resource_header(req_id, b'shop')
    # 3. С”РґРёРЅРёР№ С„СЂР°РіРјРµРЅС‚ (flags=1 в†’ final). РЇРєС‰Рѕ blob > ~1300 Р±Р°Р№С‚ С‚СЂРµР±Р°
    # СЂРѕР·Р±РёС‚Рё РЅР° РєС–Р»СЊРєР° fragments (seq=0,1,2,..., РѕСЃС‚Р°РЅРЅС–Р№ Р· flags=1).
    MAX_FRAG = 1300
    chunks = [blob[i:i + MAX_FRAG] for i in range(0, len(blob), MAX_FRAG)] or [b'']
    for i, chunk in enumerate(chunks):
        is_last = (i == len(chunks) - 1)
        flags = 1 if is_last else 0
        msgs += build_resource_fragment(req_id, i, flags, chunk)

    send_lock = sess.setdefault('send_lock', threading.RLock())
    try:
        with send_lock:
            pkt = build_channel_packet(msgs, sess, reliable=True)
            pkt = bw_encrypt_packet(pkt, sess['bf_key'])
            runtime_sendto(sock, pkt, addr)
    except Exception:
        return
    print(f"    [>] Shop STREAM: req={req_id}, blob={len(blob)}B "
          f"in {len(chunks)} fragment(s)")


def build_account_event_noargs(msg_id: int) -> bytes:
    """Account.<event>() Р±РµР· Р°СЂРіСѓРјРµРЅС‚С–РІ вЂ” entityMessage Р· Р»РёС€Рµ EntityID(4B)."""
    em = struct.pack('<I', PLAYER_ENTITY_ID)
    return msg_varlen(msg_id, em)


# в”Ђв”Ђ Arena/Battle constants в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
# Arena GUI types (Р· res/scripts/common/constants.py):
#   0=UNKNOWN, 1=RANDOM, 2=TRAINING, 3=COMPANY, 4=TOURNAMENT.
ARENA_GUI_TYPE_RANDOM = 1
ARENA_PERIOD_WAITING = 1
ARENA_PERIOD_PREBATTLE = 2
ARENA_PERIOD_BATTLE = 3
ARENA_PERIOD_AFTERBATTLE = 4
ARENA_UPDATE_VEHICLE_LIST = 1
ARENA_UPDATE_VEHICLE_ADDED = 2
ARENA_UPDATE_PERIOD = 3
ARENA_UPDATE_STATISTICS = 4
ARENA_UPDATE_VEHICLE_STATISTICS = 5
ARENA_UPDATE_VEHICLE_KILLED = 6
ARENA_UPDATE_AVATAR_READY = 7
ARENA_UPDATE_BASE_POINTS = 8
ARENA_UPDATE_BASE_CAPTURED = 9
BATTLE_FINISH_REASON_EXTERMINATION = 1
BATTLE_FINISH_REASON_BASE = 2
# Arena type IDs Р· res/scripts/arena_defs/_list_.xml:
#   1=01_karelia, 2=02_malinovka, 4=04_himmelsdorf, 5=05_prohorovka,
#   6=06_ensk, 7=07_lakeville, 8=08_ruinberg, 10=10_hills, 11=11_murovanka,
#   13=13_erlenberg, 15=15_komarin, 18=18_cliff, 19=19_monastery,
#   23=23_westfeld, 28=28_desert, 29=29_el_hallouf, 34=34_redshire,
#   35=35_steppes, 37=37_caucasus, 38=38_mannerheim_line.
ARENA_TYPE_KARELIA   = 1
PREBATTLE_TIMER_SECONDS = int(get_value(
    CONFIG, 'battle.prebattle_timer_seconds', 25))
BATTLE_TIMER_SECONDS = int(get_value(CONFIG, 'battle.battle_timer_seconds', 900))
BATTLE_READY_GUARD_SECONDS = float(get_value(
    CONFIG, 'battle.ready_guard_seconds', 1.0))
BATTLE_PERIOD_TIME_OFFSET_SECONDS = float(get_value(
    CONFIG, 'battle.period_time_offset_seconds', 3.0))
MATCHMAKING_MIN_SECONDS = float(get_value(
    CONFIG, 'matchmaking.min_seconds', 15))
MATCHMAKING_MAX_SECONDS = float(get_value(
    CONFIG, 'matchmaking.max_seconds', 20))
MATCHMAKING_TEAM_BALANCE_MODE = str(get_value(
    CONFIG, 'matchmaking.team_balance_mode', 'health_weight'))
MATCHMAKING_TEAM_WEIGHT_FIELD = str(get_value(
    CONFIG, 'matchmaking.team_weight_field', 'maxHealth'))
MATCHMAKING_QUEUE_FAKE_FILLERS = max(0, int(get_value(
    CONFIG, 'matchmaking.queue_fake_fillers', 0)))
BATTLE_TICK_HZ = max(10.0, min(200.0, float(get_value(
    CONFIG, 'battle.tick_hz', 60.0))))
BATTLE_MOTION_TICK = 1.0 / BATTLE_TICK_HZ
BATTLE_VERBOSE_DEBUG = bool(get_value(CONFIG, 'battle.verbose_debug', False))
BASE_CAPTURE_RADIUS = float(get_value(
    CONFIG, 'battle.base_capture_radius', 50.0))
BASE_CAPTURE_POINTS_MAX = int(get_value(
    CONFIG, 'battle.base_capture_points_max', 100))
BASE_CAPTURE_POINTS_PER_SECOND = float(get_value(
    CONFIG, 'battle.base_capture_points_per_second', 1.0))
BASE_CAPTURE_MAX_POINTS_PER_SECOND = float(get_value(
    CONFIG, 'battle.base_capture_max_points_per_second', 3.0))
BASE_CAPTURE_CLIENT_POSITION_MAX_AGE = float(get_value(
    CONFIG, 'battle.base_capture_client_position_max_age', 5.0))
TARGETING_UPDATE_INTERVAL = float(get_value(
    CONFIG, 'battle.targeting_update_interval', 0.08))
BATTLE_DEFAULT_FORWARD_SPEED = float(get_value(
    CONFIG, 'combat.default_forward_speed', 10.0)) * VEHICLE_SPEED_MULTIPLIER
BATTLE_DEFAULT_BACKWARD_SPEED = float(get_value(
    CONFIG, 'combat.default_backward_speed', 4.0)) * VEHICLE_SPEED_MULTIPLIER
BATTLE_DEFAULT_ROTATION_SPEED = math.radians(float(get_value(
    CONFIG, 'combat.default_rotation_speed_degrees', 30.0))) * VEHICLE_ROTATION_MULTIPLIER
BATTLE_DEFAULT_ACCEL = float(get_value(
    CONFIG, 'combat.default_accel', 1.8)) * VEHICLE_SPEED_MULTIPLIER
BATTLE_DEFAULT_DECEL = float(get_value(
    CONFIG, 'combat.default_decel', 6.0)) * VEHICLE_SPEED_MULTIPLIER
BATTLE_DEFAULT_ROT_ACCEL = float(get_value(
    CONFIG, 'combat.default_rot_accel', 2.0)) * VEHICLE_ROTATION_MULTIPLIER
BATTLE_DEFAULT_ROT_DECEL = float(get_value(
    CONFIG, 'combat.default_rot_decel', 3.0)) * VEHICLE_ROTATION_MULTIPLIER
BATTLE_ROTATION_SPEED_FACTOR = float(get_value(
    CONFIG, 'combat.rotation_speed_factor', 1.8))
BATTLE_ROTATION_ACCEL_FACTOR_XML = float(get_value(
    CONFIG, 'combat.rotation_accel_factor_xml', 2.0))
BATTLE_ROTATION_BOOST_FULL_WEIGHT_T = float(get_value(
    CONFIG, 'combat.rotation_boost_full_weight_t', 35.0))
BATTLE_ROTATION_BOOST_NONE_WEIGHT_T = float(get_value(
    CONFIG, 'combat.rotation_boost_none_weight_t', 55.0))
BATTLE_LIGHT_ROT_ACCEL_WEIGHT_T = float(get_value(
    CONFIG, 'combat.light_rot_accel_weight_t', 18.0))
BATTLE_LIGHT_ROT_ACCEL_HP_PER_TON = float(get_value(
    CONFIG, 'combat.light_rot_accel_hp_per_ton', 16.0))
BATTLE_LIGHT_ROT_ACCEL_BONUS = float(get_value(
    CONFIG, 'combat.light_rot_accel_bonus', 1.45))
BATTLE_ACCEL_HP_PER_TON_FACTOR = float(get_value(
    CONFIG, 'combat.accel_hp_per_ton_factor', 0.18))
BATTLE_ACCEL_MIN = float(get_value(
    CONFIG, 'combat.accel_min', 0.6)) * VEHICLE_SPEED_MULTIPLIER
BATTLE_ACCEL_MAX = float(get_value(
    CONFIG, 'combat.accel_max', 8.0)) * VEHICLE_SPEED_MULTIPLIER
BATTLE_ACCEL_LIGHT_TOP_SPEED_TIME = float(get_value(
    CONFIG, 'combat.accel_light_top_speed_time', 4.0))
BATTLE_ACCEL_MEDIUM_TOP_SPEED_TIME = float(get_value(
    CONFIG, 'combat.accel_medium_top_speed_time', 4.8))
BATTLE_ACCEL_HEAVY_TOP_SPEED_TIME = float(get_value(
    CONFIG, 'combat.accel_heavy_top_speed_time', 5.5))
BATTLE_SPEED_DECEL_RATIO = float(get_value(
    CONFIG, 'combat.speed_decel_ratio', 3.5))
BATTLE_DECEL_MIN = float(get_value(
    CONFIG, 'combat.decel_min', 5.0)) * VEHICLE_SPEED_MULTIPLIER
BATTLE_DECEL_MAX = float(get_value(
    CONFIG, 'combat.decel_max', 24.0)) * VEHICLE_SPEED_MULTIPLIER
BATTLE_COAST_DECEL_RATIO = float(get_value(
    CONFIG, 'combat.coast_decel_ratio', 0.55))
BATTLE_COAST_DECEL_MIN = float(get_value(
    CONFIG, 'combat.coast_decel_min', 2.2))
BATTLE_COAST_DECEL_MAX = float(get_value(
    CONFIG, 'combat.coast_decel_max', 4.5))
BATTLE_ROT_ACCEL_FACTOR = float(get_value(
    CONFIG, 'combat.rot_accel_factor', 2.0))
BATTLE_ROT_ACCEL_MIN = float(get_value(
    CONFIG, 'combat.rot_accel_min', 1.0))
BATTLE_ROT_DECEL_RATIO = float(get_value(
    CONFIG, 'combat.rot_decel_ratio', 1.5))
BATTLE_ROT_DECEL_MIN = float(get_value(
    CONFIG, 'combat.rot_decel_min', 1.5))
BATTLE_MIN_TURN_FACTOR = float(get_value(
    CONFIG, 'combat.min_turn_factor', 1.0))
BATTLE_HEAVY_MIN_TURN_FACTOR = float(get_value(
    CONFIG, 'combat.heavy_min_turn_factor', 0.7))
REMOTE_GUN_PITCH_SCALE = float(get_value(
    CONFIG, 'combat.remote_gun_pitch_scale', 0.35))
REMOTE_GUN_PITCH_LIMIT = math.radians(float(get_value(
    CONFIG, 'combat.remote_gun_pitch_limit_degrees', 12.0)))
SHOT_TRACE_DISTANCE = float(get_value(
    CONFIG, 'combat.shot_trace_distance', 1200.0))
TARGET_MARKER_OCCLUSION_MAX_AGE = float(get_value(
    CONFIG, 'combat.target_marker_occlusion_max_age', 3.0))
SPOTTING_ENABLED = bool(get_value(CONFIG, 'combat.spotting_enabled', True))
SPOTTING_AUTO_REVEAL_DISTANCE = float(get_value(
    CONFIG, 'combat.spotting_auto_reveal_distance', 50.0))
SPOTTING_MAX_RANGE = float(get_value(CONFIG, 'combat.spotting_max_range', 445.0))
SPOTTING_STATIONARY_SPEED = float(get_value(
    CONFIG, 'combat.spotting_stationary_speed', 0.5))
SPOTTING_SHOT_CAMO_PENALTY_SECONDS = float(get_value(
    CONFIG, 'combat.spotting_shot_camo_penalty_seconds', 5.0))
SHOT_VISIBILITY_GRACE_SECONDS = float(get_value(
    CONFIG, 'combat.shot_visibility_grace_seconds', 1.0))
KEEP_REMOTE_ENTITIES_ON_VISIBILITY_LOSS = bool(get_value(
    CONFIG, 'combat.keep_remote_entities_on_visibility_loss', False))
SPOTTING_CAMO_SCALE = float(get_value(
    CONFIG, 'combat.spotting_camo_scale', 2.0))
SPOTTING_CAMO_CLASS_MULTIPLIERS = dict(get_value(
    CONFIG, 'combat.spotting_camo_class_multipliers', {}))
SPOTTING_VIEW_RANGE_FALLBACKS = dict(get_value(
    CONFIG, 'combat.spotting_view_range_fallbacks', {}))
SPOTTING_VIEW_RANGE_CLASS_LIMITS = dict(get_value(
    CONFIG, 'combat.spotting_view_range_class_limits', {}))
SPOTTING_CAMO_FALLBACKS = dict(get_value(
    CONFIG, 'combat.spotting_camo_fallbacks', {}))
SPOTTING_BUSH_RADIUS = float(get_value(
    CONFIG, 'combat.spotting_bush_radius', 7.5))
SPOTTING_BUSH_BONUS = float(get_value(
    CONFIG, 'combat.spotting_bush_bonus', 0.08))
SPOTTING_BUSH_MAX_BONUS = float(get_value(
    CONFIG, 'combat.spotting_bush_max_bonus', 0.25))
SPOTTING_BUSH_PATTERNS = tuple(
    str(item).lower().encode('ascii', 'ignore')
    for item in get_value(CONFIG, 'combat.spotting_bush_patterns',
                          ['bush', 'scrub', 'juniper', 'shrub', 'pampas'])
)
CLIENT_POSITION_MAX_AGE = float(get_value(
    CONFIG, 'combat.client_position_max_age', 0.75))
CLIENT_AVATAR_VEHICLE_POS_MAX_DELTA = float(get_value(
    CONFIG, 'combat.client_avatar_vehicle_pos_max_delta', 80.0))
CLIENT_AUTHORITATIVE_VEHICLE_CONTROL = bool(get_value(
    CONFIG, 'combat.client_authoritative_vehicle_control', False))
OWN_VEHICLE_SYNC_INTERVAL = max(0.0, float(get_value(
    CONFIG, 'combat.own_vehicle_sync_interval', 0.0)))
FORCED_POSITION_BROADCAST_INTERVAL = float(get_value(
    CONFIG, 'combat.forced_position_broadcast_interval', 0.0))
FORCED_POSITION_ON_FIRST_MOTION = bool(get_value(
    CONFIG, 'combat.forced_position_on_first_motion', False))
SHOT_TANK_CENTER_HEIGHT = float(get_value(
    CONFIG, 'combat.shot_tank_center_height', 1.3))
SHOT_TANK_HALF_LENGTH = float(get_value(
    CONFIG, 'combat.shot_tank_half_length', 5.2))
SHOT_TANK_HALF_WIDTH = float(get_value(
    CONFIG, 'combat.shot_tank_half_width', 2.4))
SHOT_TANK_MIN_HEIGHT = float(get_value(
    CONFIG, 'combat.shot_tank_min_height', 0.15))
SHOT_TANK_MAX_HEIGHT = float(get_value(
    CONFIG, 'combat.shot_tank_max_height', 3.8))
SHOT_TANK_HIT_RADIUS_H = float(get_value(
    CONFIG, 'combat.shot_tank_hit_radius_h', 7.0))
SHOT_TANK_HIT_RADIUS_V = float(get_value(
    CONFIG, 'combat.shot_tank_hit_radius_v', 6.0))
SHOT_TANK_MARKER_VERT_ABOVE = float(get_value(
    CONFIG, 'combat.shot_tank_marker_vert_above', 25.0))
SHOT_GUN_ALIGNMENT_TOLERANCE_COS = math.cos(math.radians(float(get_value(
    CONFIG, 'combat.shot_gun_alignment_tolerance_degrees', 35.0))))
SHOT_DISPERSION_ENABLED = bool(get_value(
    CONFIG, 'combat.shot_dispersion_enabled', True))
SHOT_HIT_CHANCE_PERCENT = float(get_value(
    CONFIG, 'combat.shot_hit_chance_percent', 70.0))
SHOT_DISPERSION_RADIUS_AT_100M = float(get_value(
    CONFIG, 'combat.shot_dispersion_radius_at_100m', 2.5))
SHOT_DISPERSION_SERVER_RADIUS_SCALE = max(0.05, min(1.0, float(get_value(
    CONFIG, 'combat.shot_dispersion_server_radius_scale', 0.45))))
SHOT_DISPERSION_CENTER_BIAS = max(0.25, min(8.0, float(get_value(
    CONFIG, 'combat.shot_dispersion_center_bias', 2.5))))
SHOT_DISPERSION_MIN_RADIUS = float(get_value(
    CONFIG, 'combat.shot_dispersion_min_radius', 0.25))
SHOT_DISPERSION_MAX_RADIUS = float(get_value(
    CONFIG, 'combat.shot_dispersion_max_radius', 30.0))
SHOT_DAMAGE_RANDOMIZATION = max(0.0, min(0.95, float(get_value(
    CONFIG, 'combat.shot_damage_randomization', 0.25))))
SHOT_AP_NONPEN_DAMAGE_FACTOR = max(0.0, min(1.0, float(get_value(
    CONFIG, 'combat.shot_ap_nonpen_damage_factor', 0.0))))
SHOT_RICOCHET_DAMAGE_FACTOR = max(0.0, min(1.0, float(get_value(
    CONFIG, 'combat.shot_ricochet_damage_factor', 0.0))))
SHOT_RESULT_RICOCHET = 0
SHOT_RESULT_ARMOR_NOT_PIERCED = 1
SHOT_RESULT_ARMOR_PIERCED_NO_DAMAGE = 2
SHOT_RESULT_ARMOR_PIERCED = 3
SHOT_RESULT_CRITICAL_HIT = 4
SHOT_ARMOR_MIN_COS = float(get_value(CONFIG, 'combat.shot_armor_min_cos', 0.12))
SHOT_ARMOR_AUTORICOCHET_COS = math.cos(math.radians(float(get_value(
    CONFIG, 'combat.shot_armor_autoricochet_degrees', 70.0))))
SHOT_PENETRATION_NEAR_DISTANCE = float(get_value(
    CONFIG, 'combat.shot_penetration_near_distance', 100.0))
SHOT_PENETRATION_FAR_DISTANCE = float(get_value(
    CONFIG, 'combat.shot_penetration_far_distance', 500.0))
SHOT_PENETRATION_FACTOR = max(0.0, float(get_value(
    CONFIG, 'combat.shot_penetration_factor', 1.0)))
SHOT_HE_NONPEN_DAMAGE_FACTOR = float(get_value(
    CONFIG, 'combat.shot_he_nonpen_damage_factor', 0.18))
SHOT_HE_MIN_SPLASH_DAMAGE = int(get_value(
    CONFIG, 'combat.shot_he_min_splash_damage', 1))
ARTILLERY_SPLASH_MIN_FACTOR = float(get_value(
    CONFIG, 'combat.artillery_splash_min_factor', 0.35))
ARTILLERY_DIRECT_HE_DAMAGE_FACTOR = float(get_value(
    CONFIG, 'combat.artillery_direct_he_damage_factor', 0.55))
ARTILLERY_SPLASH_RADIUS_FACTOR = float(get_value(
    CONFIG, 'combat.artillery_splash_radius_factor', 2.0))
ARTILLERY_SPLASH_CALIBER_FACTOR = float(get_value(
    CONFIG, 'combat.artillery_splash_caliber_factor', 15.0))
ARTILLERY_SPLASH_MAX_RADIUS = float(get_value(
    CONFIG, 'combat.artillery_splash_max_radius', 18.0))
ARTILLERY_DIRECT_HIT_RADIUS_H = float(get_value(
    CONFIG, 'combat.artillery_direct_hit_radius_h', 3.4))
ARTILLERY_DIRECT_HIT_RADIUS_V = float(get_value(
    CONFIG, 'combat.artillery_direct_hit_radius_v', 5.5))
ARTILLERY_FLIGHT_TIME_MIN = float(get_value(
    CONFIG, 'combat.artillery_flight_time_min', 0.45))
ARTILLERY_FLIGHT_TIME_MAX = float(get_value(
    CONFIG, 'combat.artillery_flight_time_max', 4.5))
ARTILLERY_VISIBLE_TRACER_TIME = float(get_value(
    CONFIG, 'combat.artillery_visible_tracer_time', 1.6))
ARTILLERY_VISIBLE_TRACER_BACK_DISTANCE = float(get_value(
    CONFIG, 'combat.artillery_visible_tracer_back_distance', 70.0))
ARTILLERY_VISIBLE_TRACER_HEIGHT = float(get_value(
    CONFIG, 'combat.artillery_visible_tracer_height', 80.0))
ARTILLERY_VISIBLE_TRACER_GRAVITY_FACTOR = float(get_value(
    CONFIG, 'combat.artillery_visible_tracer_gravity_factor', 0.15))
ARTILLERY_VISIBLE_TRACER_MIN_VX = float(get_value(
    CONFIG, 'combat.artillery_visible_tracer_min_vx', 8.0))
ARTILLERY_OBSTACLE_BLOCKS_SHOT = bool(get_value(
    CONFIG, 'combat.artillery_obstacle_blocks_shot', True))
ARTILLERY_DESCENT_OBSTACLE_TARGET_GAP = float(get_value(
    CONFIG, 'combat.artillery_descent_obstacle_target_gap', 2.0))
ARTILLERY_SPLASH_OBSTACLE_TARGET_GAP = float(get_value(
    CONFIG, 'combat.artillery_splash_obstacle_target_gap', 0.0))
HULL_AIM_AUTOROTATION_ENABLED = bool(get_value(
    CONFIG, 'combat.hull_aim_autorotation_enabled', True))
HULL_AIM_AUTOROTATION_MIN_DISTANCE = float(get_value(
    CONFIG, 'combat.hull_aim_autorotation_min_distance', 5.0))
HULL_AIM_AUTOROTATION_DEAD_ANGLE = float(get_value(
    CONFIG, 'combat.hull_aim_autorotation_dead_angle', math.radians(2.5)))
DIRECT_PROJECTILE_IMPACT_MIN_DELAY = float(get_value(
    CONFIG, 'combat.direct_projectile_impact_min_delay', 0.08))
DIRECT_PROJECTILE_IMPACT_MAX_DELAY = float(get_value(
    CONFIG, 'combat.direct_projectile_impact_max_delay', 0.35))
REMOTE_SHOT_INTRO_DELAY = float(get_value(
    CONFIG, 'combat.remote_shot_intro_delay', 0.05))
REMOTE_ENTITY_INTRO_GRACE_SECONDS = float(get_value(
    CONFIG, 'combat.remote_entity_intro_grace_seconds', 0.5))
REMOTE_SHOT_SOUND_DELAY = float(get_value(
    CONFIG, 'combat.remote_shot_sound_delay', 0.25))
TARGET_HIT_RADIUS = float(get_value(CONFIG, 'combat.target_hit_radius', 8.0))
TARGET_AIM_RADIUS = float(get_value(CONFIG, 'combat.target_aim_radius', 8.0))
SHOT_TARGET_OVERSHOOT = float(get_value(
    CONFIG, 'combat.shot_target_overshoot', 10.0))
STATIC_OBSTACLE_MOVE_RADIUS_FACTOR = float(get_value(
    CONFIG, 'combat.static_obstacle_move_radius_factor', 0.5))
STATIC_OBSTACLE_MOVE_RADIUS_MIN = float(get_value(
    CONFIG, 'combat.static_obstacle_move_radius_min', 2.8))
STATIC_OBSTACLE_MOVE_RADIUS_MAX = float(get_value(
    CONFIG, 'combat.static_obstacle_move_radius_max', 4.5))
STATIC_OBSTACLE_SHOT_RADIUS_PAD = float(get_value(
    CONFIG, 'combat.static_obstacle_shot_radius_pad', 1.0))
STATIC_OBSTACLE_SHOOTER_GAP = float(get_value(
    CONFIG, 'combat.static_obstacle_shooter_gap', 4.0))
STATIC_OBSTACLE_TARGET_GAP = float(get_value(
    CONFIG, 'combat.static_obstacle_target_gap', 4.0))
STATIC_OBSTACLE_Y_BELOW = float(get_value(
    CONFIG, 'combat.static_obstacle_y_below', 1.5))
STATIC_OBSTACLE_Y_HEIGHT = float(get_value(
    CONFIG, 'combat.static_obstacle_y_height', 5.0))
STATIC_OBSTACLE_CHUNK_MARGIN = float(get_value(
    CONFIG, 'combat.static_obstacle_chunk_margin', 80.0))
STATIC_OBSTACLE_TERRAIN_Y_TOLERANCE = float(get_value(
    CONFIG, 'combat.static_obstacle_terrain_y_tolerance', 3.5))
STATIC_OBSTACLE_FOOTPRINT_SHRINK = max(0.05, min(1.0, float(get_value(
    CONFIG, 'combat.static_obstacle_footprint_shrink', 0.75))))
STATIC_OBSTACLE_TANK_HALO = max(0.0, float(get_value(
    CONFIG, 'combat.static_obstacle_tank_halo', 0.8)))
STATIC_OBSTACLE_INDEX_CELL = max(8.0, float(get_value(
    CONFIG, 'combat.static_obstacle_index_cell', 50.0)))
TANK_COLLISION_RADIUS = float(get_value(
    CONFIG, 'combat.tank_collision_radius', 1.0))
TANK_COLLISION_ENABLED = bool(get_value(
    CONFIG, 'combat.tank_collision_enabled', True))
TANK_COLLISION_HALF_WIDTH_SCALE = max(0.1, min(1.0, float(get_value(
    CONFIG, 'combat.tank_collision_half_width_scale', 0.85))))
TANK_COLLISION_HALF_LENGTH_SCALE = max(0.1, min(1.0, float(get_value(
    CONFIG, 'combat.tank_collision_half_length_scale', 0.55))))
TANK_COLLISION_MARGIN = max(0.0, float(get_value(
    CONFIG, 'combat.tank_collision_margin', 0.05)))
TANK_COLLISION_SWEEP_STEP = max(0.25, float(get_value(
    CONFIG, 'combat.tank_collision_sweep_step', 1.0)))
RAM_DAMAGE_ENABLED = bool(get_value(
    CONFIG, 'combat.ram_damage_enabled', True))
RAM_MIN_CLOSING_SPEED = max(0.0, float(get_value(
    CONFIG, 'combat.ram_min_closing_speed', 6.0)))
RAM_MAX_CLOSING_SPEED = max(RAM_MIN_CLOSING_SPEED, float(get_value(
    CONFIG, 'combat.ram_max_closing_speed', 14.0)))
RAM_DAMAGE_SCALE = max(0.0, float(get_value(
    CONFIG, 'combat.ram_damage_scale', 0.006)))
RAM_DAMAGE_COOLDOWN_SECONDS = max(0.0, float(get_value(
    CONFIG, 'combat.ram_damage_cooldown_seconds', 0.75)))
RAM_DAMAGE_CONTACT_MARGIN = max(0.0, float(get_value(
    CONFIG, 'combat.ram_damage_contact_margin', 0.0)))
RAM_FRIENDLY_DAMAGE = bool(get_value(
    CONFIG, 'combat.ram_friendly_damage', False))
RAM_PUSH_DISTANCE = max(0.0, float(get_value(
    CONFIG, 'combat.ram_push_distance', 0.6)))
TANK_SLOPE_LIMIT_TAN = math.tan(math.radians(float(get_value(
    CONFIG, 'combat.tank_slope_limit_degrees', 35.0))))
TANK_SLOPE_SOFT_TAN = math.tan(math.radians(float(get_value(
    CONFIG, 'combat.tank_slope_soft_degrees', 30.0))))
TANK_SLOPE_SAMPLE_MIN_XZ = float(get_value(
    CONFIG, 'combat.tank_slope_sample_min_xz', 1.0))
TANK_SLOPE_SMOOTHING = float(get_value(
    CONFIG, 'combat.tank_slope_smoothing', 0.9))
BATTLE_TERRAIN_CHUNK_SIZE = float(get_value(
    CONFIG, 'combat.battle_terrain_chunk_size', 100.0))
BATTLE_TERRAIN_VISIBLE_OFFSET = int(get_value(
    CONFIG, 'combat.battle_terrain_visible_offset', 2))
BATTLE_TERRAIN_NORMAL_STEP = float(get_value(
    CONFIG, 'combat.battle_terrain_normal_step', 1.5))
BATTLE_ENGINE_ACCEL_FACTOR = float(get_value(
    CONFIG, 'combat.battle_engine_accel_factor', 0.18))
BATTLE_BRAKE_FORCE_FACTOR = float(get_value(
    CONFIG, 'combat.battle_brake_force_factor', 0.38))
BATTLE_ROLLING_RESISTANCE_FACTOR = float(get_value(
    CONFIG, 'combat.battle_rolling_resistance_factor', 0.35))
BATTLE_STOP_SPEED = float(get_value(CONFIG, 'combat.battle_stop_speed', 0.035))
BATTLE_STOP_ROT_SPEED = float(get_value(
    CONFIG, 'combat.battle_stop_rot_speed', 0.004))
BATTLE_TERRAIN_BLOCK_CACHE = {}
BATTLE_TERRAIN_WARNED = set()

# Arena typeID в†’ geometry path (РёР· res/scripts/arena_defs/<typeName>.xml)
# РїР°СЂР°РјРµС‚СЂ <geometry>. Р‘РµР· trailing slash РєР»С–С”РЅС‚ СЃР°Рј РґРѕРґР°С”.
ARENA_GEOMETRY_PATH = {
    1:  b'spaces/01_karelia/',
    2:  b'spaces/02_malinovka/',
    4:  b'spaces/04_himmelsdorf/',
    5:  b'spaces/05_prohorovka/',
    6:  b'spaces/06_ensk/',
    7:  b'spaces/07_lakeville/',
    8:  b'spaces/08_ruinberg/',
    10: b'spaces/10_hills/',
    11: b'spaces/11_murovanka/',
    13: b'spaces/13_erlenberg/',
    15: b'spaces/15_komarin/',
    18: b'spaces/18_cliff/',
    19: b'spaces/19_monastery/',
    23: b'spaces/23_westfeld/',
    28: b'spaces/28_desert/',
    29: b'spaces/29_el_hallouf/',
    34: b'spaces/34_redshire/',
    35: b'spaces/35_steppes/',
    37: b'spaces/37_caucasus/',
    38: b'spaces/38_mannerheim_line/',
}

# 01_karelia spawn markers extracted from the original packed chunk files:
# res/spaces/01_karelia/*.chunk -> SpawnPoint/team=1/Transform.
# BigWorld chunk transform stores local coordinates; world position is
# chunkCoord * 100 + transform translation.
KARELIA_TEAM1_SPAWNS = [
    (-273.0, 38.8, -260.0),
    (-259.0, 38.6, -273.0),
    (-245.0, 38.4, -258.0),
    (-270.0, 38.7, -240.0),
    (-252.0, 38.5, -236.0),
    (-235.0, 38.3, -246.0),
    (-260.0, 38.5, -222.0),
    (-240.0, 38.3, -222.0),
    (-222.0, 38.1, -235.0),
]

KARELIA_TEAM2_SPAWNS = [
    (-x, y, -z)
    for x, y, z in KARELIA_TEAM1_SPAWNS
]

ARENA_CAPTURE_BASE_POSITIONS = {
    1: {
        1: (-405.14, -398.27),
        2: (396.27, 402.37),
    },
}

ARENA_EXTRA_DATA = {
    'localized_data': {
        'en': {'event_name': 'Random Battle', 'session_name': 'Standard'},
        'EN': {'event_name': 'Random Battle', 'session_name': 'Standard'},
        'ru': {'event_name': 'Random Battle', 'session_name': 'Standard'},
        'RU': {'event_name': 'Random Battle', 'session_name': 'Standard'},
        'uk': {'event_name': 'Random Battle', 'session_name': 'Standard'},
        'UA': {'event_name': 'Random Battle', 'session_name': 'Standard'},
    },
    'opponents': {
        1: {'name': 'Team 1'},
        2: {'name': 'Team 2'},
        '1': {'name': 'Team 1'},
        '2': {'name': 'Team 2'},
    },
}

ARENA_SPAWN_POS = {
    1:  KARELIA_TEAM1_SPAWNS[0],
    2:  (-350.0, 80.0, -350.0),
    4:  (-330.0, 80.0, -330.0),
    5:  (-360.0, 80.0, -360.0),
    6:  (-260.0, 80.0, -260.0),
    7:  (-350.0, 80.0, -350.0),
    8:  (-330.0, 80.0, -330.0),
    10: (-350.0, 80.0, -350.0),
    11: (-360.0, 80.0, -360.0),
    13: (-350.0, 80.0, -350.0),
    15: (-330.0, 80.0, -330.0),
    18: (-350.0, 80.0, -350.0),
    19: (-350.0, 80.0, -350.0),
    23: (-350.0, 80.0, -350.0),
    28: (-360.0, 80.0, -360.0),
    29: (-360.0, 80.0, -360.0),
    34: (-360.0, 80.0, -360.0),
    35: (-360.0, 80.0, -360.0),
    37: (-350.0, 80.0, -350.0),
    38: (-350.0, 80.0, -350.0),
}


def _tuple_float(values, size):
    if not isinstance(values, (list, tuple)) or len(values) < size:
        return None
    try:
        return tuple(float(values[i]) for i in range(size))
    except (TypeError, ValueError):
        return None


def _int_keyed_map(value, mapper):
    out = {}
    for key, item in dict(value or {}).items():
        try:
            mapped_key = int(key)
        except (TypeError, ValueError):
            continue
        mapped_value = mapper(item)
        if mapped_value is not None:
            out[mapped_key] = mapped_value
    return out


def _normalize_arena_extra_data(value):
    source = dict(value or {})
    extra = {}
    localized = source.get('localized_data')
    if isinstance(localized, dict):
        cleaned = {}
        for lang, lang_data in localized.items():
            if not isinstance(lang_data, dict):
                continue
            event_name = str(lang_data.get('event_name') or '').strip()
            session_name = str(lang_data.get('session_name') or '').strip()
            cleaned[str(lang)] = {
                'event_name': event_name,
                'session_name': session_name,
            }
        if cleaned:
            extra['localized_data'] = cleaned
    opponents = source.get('opponents')
    if isinstance(opponents, dict):
        cleaned = {}
        for team, data in opponents.items():
            if not isinstance(data, dict):
                continue
            name = str(data.get('name') or '').strip()
            if not name:
                continue
            cleaned[str(team)] = {'name': name}
            try:
                cleaned[int(team)] = {'name': name}
            except (TypeError, ValueError):
                pass
        if cleaned:
            extra['opponents'] = cleaned
    for key, data in source.items():
        if key not in ('localized_data', 'opponents'):
            extra[key] = data
    return extra


def build_arena_extra_data(arena_type_id: int = ARENA_TYPE_KARELIA):
    extra = {}
    for key, value in ARENA_EXTRA_DATA.items():
        if isinstance(value, dict):
            extra[key] = {sub_key: dict(sub_value)
                          if isinstance(sub_value, dict) else sub_value
                          for sub_key, sub_value in value.items()}
        else:
            extra[key] = value
    return extra


def _load_map_settings():
    global ARENA_GEOMETRY_PATH, ARENA_SPAWN_POS
    global ARENA_CAPTURE_BASE_POSITIONS, ARENA_TEAM_SPAWNS
    global ARENA_EXTRA_DATA
    maps_cfg = get_value(CONFIG, 'maps', {}) or {}
    arena_extra = _normalize_arena_extra_data(
        maps_cfg.get('arena_extra_data'))
    if arena_extra:
        ARENA_EXTRA_DATA = arena_extra
    geometry = _int_keyed_map(
        maps_cfg.get('geometry_paths'),
        lambda value: str(value).encode('ascii', 'ignore'))
    if geometry:
        ARENA_GEOMETRY_PATH.update(geometry)
    default_spawns = _int_keyed_map(
        maps_cfg.get('default_spawns'),
        lambda value: _tuple_float(value, 3))
    if default_spawns:
        ARENA_SPAWN_POS.update(default_spawns)
    team_spawns = {}
    for arena_id, by_team in dict(maps_cfg.get('team_spawns') or {}).items():
        try:
            arena_key = int(arena_id)
        except (TypeError, ValueError):
            continue
        team_spawns[arena_key] = {}
        for team_id, positions in dict(by_team or {}).items():
            try:
                team_key = int(team_id)
            except (TypeError, ValueError):
                continue
            parsed = [_tuple_float(pos, 3) for pos in list(positions or [])]
            parsed = [pos for pos in parsed if pos is not None]
            if parsed:
                team_spawns[arena_key][team_key] = parsed
    ARENA_TEAM_SPAWNS = {
        ARENA_TYPE_KARELIA: {
            1: KARELIA_TEAM1_SPAWNS,
            2: KARELIA_TEAM2_SPAWNS,
        }
    }
    for arena_id, by_team in team_spawns.items():
        ARENA_TEAM_SPAWNS.setdefault(arena_id, {}).update(by_team)
    capture_bases = {}
    for arena_id, by_base in dict(maps_cfg.get('capture_bases') or {}).items():
        try:
            arena_key = int(arena_id)
        except (TypeError, ValueError):
            continue
        capture_bases[arena_key] = {}
        for base_id, value in dict(by_base or {}).items():
            try:
                base_key = int(base_id)
            except (TypeError, ValueError):
                continue
            parsed = _tuple_float(value, 2)
            if parsed is not None:
                capture_bases[arena_key][base_key] = parsed
    if capture_bases:
        for arena_id, by_base in capture_bases.items():
            ARENA_CAPTURE_BASE_POSITIONS.setdefault(arena_id, {}).update(by_base)
    enabled = maps_cfg.get('enabled_arena_type_ids')
    if enabled:
        arena_ids = set()
        for arena_id in enabled:
            try:
                arena_ids.add(int(arena_id))
            except (TypeError, ValueError):
                pass
    else:
        arena_ids = set(ARENA_GEOMETRY_PATH)
    fallback = int(maps_cfg.get('fallback_arena_type_id') or ARENA_TYPE_KARELIA)
    if fallback not in ARENA_GEOMETRY_PATH:
        fallback = ARENA_TYPE_KARELIA
    arena_ids = {arena_id for arena_id in arena_ids if arena_id in ARENA_GEOMETRY_PATH}
    if fallback not in arena_ids:
        arena_ids.add(fallback)
    return fallback, arena_ids


ARENA_TYPE_FALLBACK, ENABLED_ARENA_TYPE_IDS = _load_map_settings()

STATIC_OBSTACLE_MODEL_RE = re.compile(
    rb'content/(?:Environment|Buildings|BuildingsRare|MillitaryInstallations)/[^\x00]{0,200}\.modelx?')
SPOTTING_BUSH_RE = re.compile(rb'speedtree/[^\x00]{1,200}\.spt')
STATIC_OBSTACLE_CACHE = {}
STATIC_OBSTACLE_INDEX_CACHE = {}
SPOTTING_BUSH_CACHE = {}
SPOTTING_BUSH_INDEX_CACHE = {}
STATIC_MODEL_RADIUS_CACHE = {}
STATIC_MODEL_BOUNDS_CACHE = {}


def normalize_arena_type_id(arena_type_id) -> int:
    try:
        arena_type_id = int(arena_type_id)
    except (TypeError, ValueError):
        return ARENA_TYPE_FALLBACK
    if arena_type_id not in ENABLED_ARENA_TYPE_IDS:
        return ARENA_TYPE_FALLBACK
    if arena_type_id not in ARENA_GEOMETRY_PATH:
        return ARENA_TYPE_FALLBACK
    return arena_type_id

# spaceData keys (Р· common/space_data_types.hpp)
SPACE_DATA_TOD_KEY                  = 0    # SpaceData_ToDData (8B: 2 floats)
SPACE_DATA_MAPPING_KEY_CLIENT_SERVER = 1   # 4x4 matrix + path в†’ addMapping
SPACE_DATA_MAPPING_KEY_CLIENT_ONLY   = 2


def build_space_data_message(space_id: int, key: int, data: bytes,
                             entry_id: bytes = b'\x00' * 8) -> bytes:
    """spaceData (msgID 0x07, varlen 2B). Р¤РѕСЂРјР°С‚ (server_connection.cpp:1845):
       SpaceID(4) + SpaceEntryID(8 = ip4+port2+salt2) + key(2) + data(rest).

       SpaceEntryID вЂ” С†Рµ Mercury::Address; РґР»СЏ РЅР°С€РѕС— "СЃРёРјСѓР»СЊРѕРІР°РЅРѕС—" РјР°РїРё
       РјРѕР¶РЅР° 8 РЅСѓР»СЊРѕРІРёС… Р±Р°Р№С‚ (СѓРЅС–РєР°Р»СЊРЅРёР№ key РґРѕСЃС‚Р°С‚РЅСЊРѕ С‚СЂРёРјР°С‚Рё РїРѕ key)."""
    payload = struct.pack('<I', space_id)
    payload += entry_id
    payload += struct.pack('<H', key)
    payload += data
    return msg_varlen(0x07, payload)


def build_geometry_mapping_data(path: bytes,
                                matrix=None) -> bytes:
    """Р¤РѕСЂРјР°С‚ SpaceData_MappingData (common/space_data_types.hpp):
       float matrix[4][4] (16 floats = 64B, identity РґР»СЏ РЅР°С€РѕРіРѕ РІРёРїР°РґРєСѓ)
       + char path[] (raw bytes Р±РµР· length prefix вЂ” read РґРѕ РєС–РЅС†СЏ stream)."""
    if matrix is None:
        # Identity matrix 4x4
        matrix = (1.0, 0.0, 0.0, 0.0,
                  0.0, 1.0, 0.0, 0.0,
                  0.0, 0.0, 1.0, 0.0,
                  0.0, 0.0, 0.0, 1.0)
    return struct.pack('<16f', *matrix) + path


def build_avatar_update_arena(update_type: int, data) -> bytes:
    em = struct.pack('<I', AVATAR_ENTITY_ID)
    em += struct.pack('<B', update_type)
    em += bw_pack_string(pickle.dumps(data, protocol=2))
    return msg_varlen(AVATAR_UPDATEARENA_MSG_ID, em)


def build_avatar_update_own_vehicle_position(pos, yaw: float,
                                             speed: float = 0.0,
                                             rspeed: float = 0.0) -> bytes:
    em = struct.pack('<I', AVATAR_ENTITY_ID)
    em += struct.pack('<fff', *pos)            # position VECTOR3
    em += struct.pack('<fff', yaw, 0.0, 0.0)   # direction VECTOR3: yaw,pitch,roll
    em += struct.pack('<ff', speed, rspeed)
    return msg_varlen(AVATAR_UPDATE_OWN_POSITION_MSG_ID, em)


def build_avatar_update_positions(indices, positions) -> bytes:
    em = struct.pack('<I', AVATAR_ENTITY_ID)
    em += pack_uint8_array(indices)
    em += pack_int16_array(positions)
    return msg_varlen(AVATAR_UPDATE_POSITIONS_MSG_ID, em)


def pack_direction3d(yaw: float, pitch: float = 0.0,
                     roll: float = 0.0) -> bytes:
    return struct.pack('<fff', roll, pitch, yaw)


def build_detailed_position(entity_id: int, pos, yaw: float,
                            pitch: float = 0.0,
                            roll: float = 0.0) -> bytes:
    payload = struct.pack('<I', entity_id)
    payload += struct.pack('<fff', *pos)
    payload += pack_direction3d(yaw, pitch, roll)
    return msg_fixed(CLIENT_DETAILED_POSITION_MSG_ID, payload)


def build_forced_position(entity_id: int, pos, yaw: float,
                          space_id: int = SPACE_ID,
                          vehicle_id: int = 0) -> bytes:
    payload = struct.pack('<I', entity_id)
    payload += struct.pack('<I', space_id)
    payload += struct.pack('<I', vehicle_id)
    payload += struct.pack('<fff', *pos)
    payload += pack_direction3d(yaw)
    return msg_fixed(CLIENT_FORCED_POSITION_MSG_ID, payload)


def build_control_entity(entity_id: int, enabled: bool) -> bytes:
    payload = struct.pack('<I', entity_id)
    payload += struct.pack('<B', 1 if enabled else 0)
    return msg_fixed(CLIENT_CONTROL_ENTITY_MSG_ID, payload)


def build_set_vehicle(passenger_id: int, vehicle_id: int) -> bytes:
    return msg_fixed(CLIENT_SET_VEHICLE_MSG_ID,
                     struct.pack('<II', passenger_id, vehicle_id))


def build_avatar_vehicle_bind(pos, yaw: float) -> bytes:
    return (
        build_set_vehicle(AVATAR_ENTITY_ID, PLAYER_VEHICLE_ID) +
        build_detailed_position(AVATAR_ENTITY_ID, (0.0, 0.0, 0.0), yaw) +
        build_detailed_position(PLAYER_VEHICLE_ID, pos, yaw)
    )


def build_battle_motion_sync(pos, yaw: float,
                             speed: float = 0.0,
                             rspeed: float = 0.0,
                             bind_avatar: bool = False) -> bytes:
    msgs = b''
    if bind_avatar:
        msgs += build_set_vehicle(AVATAR_ENTITY_ID, PLAYER_VEHICLE_ID)
    msgs += (
        build_forced_position(AVATAR_ENTITY_ID, (0.0, 0.0, 0.0), yaw,
                              space_id=SPACE_ID,
                              vehicle_id=PLAYER_VEHICLE_ID) +
        build_avatar_update_own_vehicle_position(pos, yaw, speed, rspeed) +
        build_vehicle_motion_update(pos, yaw)
    )
    return msgs


def build_battle_motion_tick(pos, yaw: float,
                             speed: float = 0.0,
                             rspeed: float = 0.0) -> bytes:
    return build_vehicle_motion_update(pos, yaw)


def should_send_own_vehicle_sync(sess: dict) -> bool:
    if OWN_VEHICLE_SYNC_INTERVAL <= 0.0:
        return False
    now = time.time()
    last = float(sess.get('battle_last_own_vehicle_sync_time', 0.0))
    if last > 0.0 and now - last < OWN_VEHICLE_SYNC_INTERVAL:
        return False
    sess['battle_last_own_vehicle_sync_time'] = now
    return True


def build_battle_motion_tick_for_session(sess: dict, pos, yaw: float,
                                         speed: float = 0.0,
                                         rspeed: float = 0.0) -> bytes:
    msgs = build_battle_motion_tick(pos, yaw, speed, rspeed)
    if should_send_own_vehicle_sync(sess):
        msgs += build_avatar_update_own_vehicle_position(
            pos, yaw, speed, rspeed)
    if sess.pop('battle_motion_force_position', False):
        msgs += build_forced_position(PLAYER_VEHICLE_ID, pos, yaw,
                                      space_id=SPACE_ID, vehicle_id=0)
    return msgs


def angle_to_int8(angle: float) -> int:
    value = int(math.floor((angle * 128.0) / math.pi + 0.5))
    return ((value + 128) % 256) - 128


def normalize_angle(angle: float) -> float:
    return (angle + math.pi) % (2.0 * math.pi) - math.pi


def clamp(value: float, min_value: float, max_value: float) -> float:
    return max(min_value, min(max_value, value))


def half_angle_to_int8(angle: float) -> int:
    value = int(math.floor((angle * 254.0) / math.pi + 0.5))
    return max(-128, min(127, value))


def int8_to_angle(value: int) -> float:
    if value >= 128:
        value -= 256
    return (float(value) * math.pi) / 128.0


def int8_to_half_angle(value: int) -> float:
    if value >= 128:
        value -= 256
    return (float(value) * math.pi) / 254.0


def pack_yaw_pitch_roll(yaw: float, pitch: float = 0.0,
                        roll: float = 0.0) -> bytes:
    return struct.pack('<bbb',
                       angle_to_int8(yaw),
                       half_angle_to_int8(pitch),
                       angle_to_int8(roll))


def _pack_bw_3(value: int) -> bytes:
    # BigWorld stores PackedXZ internally with BW_PACK3 (high byte first), then
    # streams it through BW_HTON3_ASSIGN on little-endian clients. On the wire
    # that is low, middle, high byte order.
    return bytes((value & 0xff, (value >> 8) & 0xff, (value >> 16) & 0xff))


def pack_packed_xz(x_value: float, z_value: float) -> bytes:
    def bits(f):
        return struct.unpack('<I', struct.pack('<f', f))[0]

    add_values = (2.0, -2.0)
    x_uint = bits(x_value + add_values[1 if x_value < 0.0 else 0])
    z_uint = bits(z_value + add_values[1 if z_value < 0.0 else 0])

    result = 0
    if ((x_uint & 0x7c000000) != 0x40000000) or \
       ((x_uint & 0x03ffc000) == 0x03ffc000):
        result |= 0x7ff000
    if ((z_uint & 0x7c000000) != 0x40000000) or \
       ((z_uint & 0x03ffc000) == 0x03ffc000):
        result |= 0x0007ff

    result |= ((x_uint >> 3) & 0x7ff000) + ((x_uint & 0x4000) >> 2)
    result |= ((z_uint >> 15) & 0x0007ff) + ((z_uint & 0x4000) >> 14)
    result &= 0x7ff7ff
    result |= (x_uint >> 8) & 0x800000
    result |= (z_uint >> 20) & 0x000800
    return _pack_bw_3(result)


def pack_packed_y(y_value: float) -> bytes:
    y = y_value + (-2.0 if y_value < 0.0 else 2.0)
    y_uint = struct.unpack('<I', struct.pack('<f', y))[0]
    packed = (y_uint >> 12) & 0x7fff
    packed |= (y_uint >> 16) & 0x8000
    return struct.pack('>H', packed)


def pack_packed_xyz(pos) -> bytes:
    return pack_packed_xz(pos[0], pos[2]) + pack_packed_y(pos[1])


def build_vehicle_avatar_update(entity_id: int, pos, yaw: float) -> bytes:
    payload = struct.pack('<I', entity_id)
    payload += pack_packed_xyz(pos)
    payload += pack_yaw_pitch_roll(yaw, 0.0, 0.0)
    return msg_fixed(CLIENT_AVATAR_UPDATE_NOALIAS_FULLPOS_YPR_MSG_ID, payload)


def build_vehicle_motion_update(pos, yaw: float) -> bytes:
    return build_detailed_position(PLAYER_VEHICLE_ID, pos, yaw)

def build_vehicle_motion_update_for(vehicle_id: int, pos, yaw: float,
                                    pitch: float = 0.0,
                                    roll: float = 0.0) -> bytes:
    return build_detailed_position(vehicle_id, pos, yaw, pitch, roll)


def decode_client_coord_ypr(payload: bytes, offset: int = 0):
    if len(payload) < offset + 15:
        return None
    pos = struct.unpack_from('<fff', payload, offset)
    yaw_b, pitch_b, roll_b = struct.unpack_from('<BBB', payload, offset + 12)
    return (
        pos,
        int8_to_angle(yaw_b),
        int8_to_half_angle(pitch_b),
        int8_to_angle(roll_b),
    )


def build_own_vehicle_motion_update(pos, yaw: float,
                                    speed: float = 0.0,
                                    rspeed: float = 0.0,
                                    force: bool = False) -> bytes:
    """Hard-sync the own camera matrix and the actual Vehicle entity.

    This is not used for every motion tick. updateOwnVehiclePosition resets the
    camera matrix provider before it reattaches to vehicle.matrix, so spamming it
    makes movement look stepped.
    """
    msgs = (
        build_avatar_update_own_vehicle_position(pos, yaw, speed, rspeed) +
        build_vehicle_motion_update(pos, yaw)
    )
    if force:
        msgs += build_forced_position(PLAYER_VEHICLE_ID, pos, yaw,
                                      space_id=SPACE_ID, vehicle_id=0)
    return msgs


def build_avatar_update_targeting_info(turret_yaw: float = 0.0,
                                       gun_pitch: float = 0.0,
                                       max_turret_rotation_speed: float = 8.0,
                                       max_gun_rotation_speed: float = 8.0,
                                       shot_mult: float = 1.0,
                                       aiming_time: float = 1.0) -> bytes:
    em = struct.pack('<I', AVATAR_ENTITY_ID)
    em += struct.pack('<ffffff',
                      turret_yaw, gun_pitch,
                      max_turret_rotation_speed, max_gun_rotation_speed,
                      shot_mult, aiming_time)
    return msg_varlen(AVATAR_UPDATE_TARGETING_INFO_MSG_ID, em)


def build_avatar_update_vehicle_health(health: int,
                                       is_crew_active: bool = True) -> bytes:
    em = struct.pack('<I', AVATAR_ENTITY_ID)
    health = int(safe_float(health, 0.0, -32768.0, 32767.0))
    em += struct.pack('<hB', health, 1 if is_crew_active else 0)
    return msg_varlen(AVATAR_UPDATE_VEHICLE_HEALTH_MSG_ID, em)


def build_avatar_update_vehicle_reload(time_left: float) -> bytes:
    em = struct.pack('<I', AVATAR_ENTITY_ID)
    em += struct.pack('<f', float(time_left))
    return msg_varlen(AVATAR_UPDATE_VEHICLE_RELOAD_MSG_ID, em)


def build_avatar_update_vehicle_ammo(compact_descr: int, quantity: int,
                                     time_remaining: int = 0) -> bytes:
    em = struct.pack('<I', AVATAR_ENTITY_ID)
    em += struct.pack('<iHh', int(compact_descr), int(quantity),
                      int(time_remaining))
    return msg_varlen(AVATAR_UPDATE_VEHICLE_AMMO_MSG_ID, em)


def build_avatar_update_vehicle_setting(code: int, value: int) -> bytes:
    em = struct.pack('<I', AVATAR_ENTITY_ID)
    em += struct.pack('<Bi', int(code), int(value))
    return msg_varlen(AVATAR_UPDATE_VEHICLE_SETTING_MSG_ID, em)


def build_avatar_update_gun_marker(shot_pos, shot_vec,
                                   dispersion_angle: float = 0.03) -> bytes:
    em = struct.pack('<I', AVATAR_ENTITY_ID)
    em += struct.pack('<fff', *shot_pos)
    em += struct.pack('<fff', *shot_vec)
    em += struct.pack('<f', dispersion_angle)
    return msg_varlen(AVATAR_UPDATE_GUN_MARKER_MSG_ID, em)


def build_avatar_show_tracer(shot_id: int, shot_pos, velocity,
                             gravity: float = 9.81,
                             effects_index: int = 0,
                             vehicle_id: int = PLAYER_VEHICLE_ID) -> bytes:
    em = struct.pack('<I', AVATAR_ENTITY_ID)
    em += struct.pack('<I', vehicle_id)
    em += struct.pack('<f', float(shot_id))
    em += struct.pack('<B', safe_effects_index(effects_index))
    em += struct.pack('<fff', *shot_pos)
    em += struct.pack('<fff', *velocity)
    em += struct.pack('<f', float(gravity))
    return msg_varlen(AVATAR_SHOW_TRACER_MSG_ID, em)


def estimate_artillery_shell_flight_time(shot_pos, impact_pos, shell_speed: float) -> float:
    shot_pos = safe_vec3(shot_pos, None)
    impact_pos = safe_vec3(impact_pos, None)
    speed = safe_float(shell_speed, 800.0, 1.0)
    if shot_pos is None or impact_pos is None:
        return ARTILLERY_FLIGHT_TIME_MIN
    dx = impact_pos[0] - shot_pos[0]
    dy = impact_pos[1] - shot_pos[1]
    dz = impact_pos[2] - shot_pos[2]
    distance = math.sqrt(dx * dx + dy * dy + dz * dz)
    return clamp(distance / speed, ARTILLERY_FLIGHT_TIME_MIN,
                 ARTILLERY_FLIGHT_TIME_MAX)


def build_artillery_visible_tracer(shot_pos, impact_pos, shot_vec, shell: dict):
    shot_pos = safe_vec3(shot_pos, (0.0, 0.0, 0.0))
    impact_pos = safe_vec3(impact_pos, shot_pos)
    shot_vec = safe_vec3(shot_vec, (0.0, 0.0, 1.0))
    dx = impact_pos[0] - shot_pos[0]
    dz = impact_pos[2] - shot_pos[2]
    horizontal = math.sqrt(dx * dx + dz * dz)
    if horizontal <= 0.001:
        dx, dz = shot_vec[0], shot_vec[2]
        horizontal = math.sqrt(dx * dx + dz * dz)
    if horizontal <= 0.001:
        dx, dz, horizontal = 0.0, 1.0, 1.0
    dir_x = dx / horizontal
    dir_z = dz / horizontal
    min_dir_x = min(
        0.95,
        ARTILLERY_VISIBLE_TRACER_MIN_VX * ARTILLERY_VISIBLE_TRACER_TIME /
        ARTILLERY_VISIBLE_TRACER_BACK_DISTANCE)
    if abs(dir_x) < min_dir_x:
        sign_x = 1.0
        if abs(dx) > 0.001:
            sign_x = 1.0 if dx > 0.0 else -1.0
        elif abs(shot_vec[0]) > 0.001:
            sign_x = 1.0 if shot_vec[0] > 0.0 else -1.0
        sign_z = 1.0
        if abs(dir_z) > 0.001:
            sign_z = 1.0 if dir_z > 0.0 else -1.0
        elif abs(shot_vec[2]) > 0.001:
            sign_z = 1.0 if shot_vec[2] > 0.0 else -1.0
        dir_x = sign_x * min_dir_x
        dir_z = sign_z * math.sqrt(max(0.0, 1.0 - dir_x * dir_x))
    start = (
        impact_pos[0] - dir_x * ARTILLERY_VISIBLE_TRACER_BACK_DISTANCE,
        impact_pos[1] + ARTILLERY_VISIBLE_TRACER_HEIGHT,
        impact_pos[2] - dir_z * ARTILLERY_VISIBLE_TRACER_BACK_DISTANCE,
    )
    gravity = clamp(
        safe_float((shell or {}).get('gravity'), 9.81, 0.0) *
        ARTILLERY_VISIBLE_TRACER_GRAVITY_FACTOR,
        12.0,
        35.0)
    shell_speed = safe_float((shell or {}).get('speed', 800.0), 800.0, 1.0)
    flight_time = estimate_artillery_shell_flight_time(
        shot_pos, impact_pos, shell_speed)
    velocity = (
        (impact_pos[0] - start[0]) / flight_time,
        (impact_pos[1] - start[1] + 0.5 * gravity * flight_time * flight_time) / flight_time,
        (impact_pos[2] - start[2]) / flight_time,
    )
    return start, velocity, gravity, normalize_vec(velocity), flight_time


def build_avatar_stop_tracer(shot_id: int, end_pos) -> bytes:
    em = struct.pack('<I', AVATAR_ENTITY_ID)
    em += struct.pack('<f', float(shot_id))
    em += struct.pack('<fff', *end_pos)
    return msg_varlen(AVATAR_STOP_TRACER_MSG_ID, em)


def build_avatar_explode_projectile(shot_id: int, effects_index: int,
                                    material_index: int, end_pos,
                                    velocity_dir) -> bytes:
    em = struct.pack('<I', AVATAR_ENTITY_ID)
    em += struct.pack('<f', float(shot_id))
    em += struct.pack('<BB', safe_effects_index(effects_index),
                      int(material_index) & 0xff)
    em += struct.pack('<fff', *end_pos)
    em += struct.pack('<fff', *velocity_dir)
    return msg_varlen(AVATAR_EXPLODE_PROJECTILE_MSG_ID, em)


def build_vehicle_show_shooting(vehicle_id: int = PLAYER_VEHICLE_ID) -> bytes:
    return msg_varlen(VEHICLE_SHOW_SHOOTING_MSG_ID,
                      struct.pack('<I', vehicle_id))


def build_vehicle_damage_from_shot(vehicle_id: int, attacker_id: int,
                                   points, effects_index: int) -> bytes:
    payload = struct.pack('<I', int(vehicle_id))
    payload += struct.pack('<I', int(attacker_id))
    payload += pack_uint64_array(points)
    payload += struct.pack('<B', safe_effects_index(effects_index))
    return msg_varlen(VEHICLE_SHOW_DAMAGE_FROM_SHOT_MSG_ID, payload)


def build_vehicle_damage_from_explosion(vehicle_id: int, attacker_id: int,
                                        center, effects_index: int) -> bytes:
    safe_center = safe_vec3(center, (0.0, 0.0, 0.0))
    payload = struct.pack('<I', int(vehicle_id))
    payload += struct.pack('<I', int(attacker_id))
    payload += struct.pack('<fff', *safe_center)
    payload += struct.pack('<B', safe_effects_index(effects_index))
    return msg_varlen(VEHICLE_SHOW_DAMAGE_FROM_EXPLOSION_MSG_ID, payload)


def build_vehicle_health_property_update(vehicle_id: int, health: int,
                                         is_crew_active: bool = True) -> bytes:
    payload = struct.pack('<I', vehicle_id)
    payload += struct.pack('<B', 2)
    health = int(safe_float(health, 0.0, -32768.0, 32767.0))
    payload += struct.pack('<Bh', 1, health)
    payload += struct.pack('<BB', 2, 1 if is_crew_active else 0)
    return msg_varlen(0x09, payload)


def build_avatar_player_bundle(arena_type_id: int = ARENA_TYPE_KARELIA,
                               arena_gui_type: int = ARENA_GUI_TYPE_RANDOM,
                               weather_preset_id: int = 0,
                               vehicle_compact_descr: bytes = None,
                               vehicle_data: dict = None,
                               player_name: str = 'qwerty',
                               team: int = 1,
                               spawn_pos=None,
                               spawn_yaw: float = 0.0,
                               initial_period: int = ARENA_PERIOD_PREBATTLE,
                               period_end_time: int = PREBATTLE_TIMER_SECONDS,
                               period_length: int = PREBATTLE_TIMER_SECONDS,
                               vehicle_list=None,
                               statistics_list=None,
                               battle_id: int = 1,
                               arena_extra_data: dict = None) -> bytes:
    """РџРµСЂРµС…С–Рґ Account в†’ Avatar РґР»СЏ РІС…РѕРґСѓ РІ Р±С–Р№.

    Bundle:
      1. resetEntities(keepPlayer=False) вЂ” Р·РІС–Р»СЊРЅСЏС” РїРѕС‚РѕС‡РЅСѓ Account player.
      2. createBasePlayer(Avatar entity) Р· 5 BASE_AND_CLIENT properties
         (РїРѕСЂСЏРґРѕРє Р· Avatar.def):
           name (STRING), arenaTypeID (INT32), arenaGuiType (UINT8),
           arenaExtraData (PYTHON), weatherPresetID (UINT8).
      3. createCellPlayer(Avatar) вЂ” РїРѕС‚СЂС–Р±РµРЅ Р±Рѕ Avatar РјР°С” CellMethods +
         Volatile.position. Р‘РµР· РЅСЊРѕРіРѕ РєР»С–С”РЅС‚ Р·Р°РІРёСЃР°С” / РїР°РґР°С” РІ C++
         Р±Рѕ РЅРµ РјР°С” cell counterpart РґРѕ СЏРєРѕРіРѕ РїСЂРёС…РѕРґСЏС‚СЊ РѕРЅРѕРІР»РµРЅРЅСЏ.
         Р¤РѕСЂРјР°С‚ (lib/connection/server_connection.cpp:1810):
           SpaceID(4) + vehicleID(4) + Position3D(3Г—float) +
           Direction3D(3Г—float yaw,pitch,roll) + cell+ownClient props.
         Avatar cell+ownClient props (РІ def order):
           state (UINT16) вЂ” РїСЂРёРїСѓСЃРєР°С”РјРѕ CELL_PUBLIC,
           team (UINT8, OWN_CLIENT),
           playerVehicleID (OBJECT_ID=uint32, OWN_CLIENT).

    Avatar.onBecomePlayer (scripts/client/Avatar.py:36) РѕРґСЂР°Р·Сѓ Р¶ СЂРѕР±РёС‚СЊ
       self.arena = ClientArena(arenaTypeID, arenaGuiType,
                                arenaExtraData, weatherPresetID)
    РґРµ ClientArena.__init__ РІРёРєР»РёРєР°С” ArenaType.g_cache.get(arenaTypeID),
    С‚РѕРјСѓ arenaTypeID РјСѓСЃРёС‚СЊ С–СЃРЅСѓРІР°С‚Рё РІ _list_.xml.
    """
    msgs = b''
    if spawn_pos is None:
        spawn_pos = ARENA_SPAWN_POS.get(arena_type_id, (-360.0, 80.0, -360.0))

    # вљ пёЏ resetEntities(False) РџР РР‘Р РђРќРћ вЂ” РІРёРєР»РёРєР°Р»Рѕ C++ crash (Р±РµР· python
    # traceback, РіСЂР° РїР°РґР°Р»Р° РїРѕРІРЅС–СЃС‚СЋ). РЈ WoT 0.6.5 createBasePlayer Р±РµР·
    # РїРѕРїРµСЂРµРґРЅСЊРѕРіРѕ reset РїСЂРѕСЃС‚Рѕ РїРµСЂРµРјРёРєР°С” player entity РЅР° РЅРѕРІСѓ; СЃС‚Р°СЂР°
    # Account entity (id=1) Р·Р°Р»РёС€Р°С”С‚СЊСЃСЏ РІ client entities map СЏРє non-player.

    # 2. createBasePlayer(Avatar)
    #    arenaExtraData РІРёРєРѕСЂРёСЃС‚РѕРІСѓС”С‚СЊСЃСЏ BattleLoadingPage С‚Р° СЂС–Р·РЅРёРјРё UI;
    #    РїРѕРєРё С‰Рѕ РїСѓСЃС‚РёР№ dict вЂ” РЅРµ РїРѕРІРёРЅРµРЅ Р»Р°РјР°С‚Рё Loading.
    if arena_extra_data is None:
        arena_extra_data = build_arena_extra_data(arena_type_id)
    arena_extra = pickle.dumps(arena_extra_data, protocol=0)

    props = b''
    player_name_bytes = player_name.encode('utf-8', 'ignore') or b'player'
    props += bw_pack_string(player_name_bytes)          # name (STRING)
    props += struct.pack('<i', arena_type_id)           # arenaTypeID (INT32)
    props += struct.pack('<B', arena_gui_type)          # arenaGuiType (UINT8)
    props += bw_pack_string(arena_extra)                # arenaExtraData (PYTHON)
    props += struct.pack('<B', weather_preset_id)       # weatherPresetID (UINT8)

    cbp = struct.pack('<I', AVATAR_ENTITY_ID) + \
          struct.pack('<H', AVATAR_ENTITY_TYPE) + \
          props
    msgs += msg_varlen(0x05, cbp)

    # 2.5 spaceData(SPACE_DATA_MAPPING_KEY_CLIENT_SERVER) вЂ” РјР°РїРёС‚СЊ geometry
    #     РґРёСЂРµРєС‚РѕСЂС–СЋ (РЅР°РїСЂРёРєР»Р°Рґ "spaces/01_karelia/") Сѓ РЅР°С€ SpaceID. Р‘РµР· С†СЊРѕРіРѕ
    #     РєР»С–С”РЅС‚ РЅРµ РїРѕС‡РёРЅР°С” Р·Р°РІР°РЅС‚Р°Р¶СѓРІР°С‚Рё С‡Р°РЅРєРё, С‚РѕРјСѓ BigWorld.wg_prefetchSpaceZip
    #     callback `Avatar.onSpaceLoaded` РќР• СЃРїСЂР°С†СЊРѕРІСѓС” в†’ __stepsTillInit
    #     Р·Р°СЃС‚СЂСЏРіР°С” РЅР° 1 С– loading screen РЅРµ Р·Р°РєСЂРёРІР°С”С‚СЊСЃСЏ.
    #     РњР°С” Р№С‚Рё Р”Рћ createCellPlayer Р±Рѕ Avatar.onBecomePlayer РѕРґСЂР°Р·Сѓ С‡РёС‚Р°С”
    #     arena.typeDescriptor С– РїРѕС‡РёРЅР°С” prefetchSpaceZip.
    geometry_path = ARENA_GEOMETRY_PATH.get(
        arena_type_id, b'spaces/01_karelia/')
    mapping_data = build_geometry_mapping_data(geometry_path)
    # РЈРЅС–РєР°Р»СЊРЅРёР№ SpaceEntryID (Mercury::Address) вЂ” С‰РѕР± GeometryMapping РЅРµ
    # РґРµРґСѓРїР»С–РєСѓРІР°РІСЃСЏ РїСЂРё reuse. РўСЂРёРјР°С”РјРѕ РґРµС‚РµСЂРјС–РЅРѕРІР°РЅРѕ РЅР° РѕСЃРЅРѕРІС– space_id+key.
    entry_id = struct.pack('<IHH', SPACE_ID, 0, int(battle_id or 1) & 0xffff)
    msgs += build_space_data_message(SPACE_ID,
                                     SPACE_DATA_MAPPING_KEY_CLIENT_SERVER,
                                     mapping_data,
                                     entry_id=entry_id)

    # 3. createCellPlayer(Avatar) вЂ” С‰РѕР± РєР»С–С”РЅС‚ РїРµСЂРµР№С€РѕРІ Р· Р·Р°СЃС‚Р°РІРєРё
    # Р·Р°РІР°РЅС‚Р°Р¶РµРЅРЅСЏ РІ СЂРµР°Р»СЊРЅРёР№ Р±С–Р№ (Avatar.onEnterWorld в†’ onEnterWorld).
    # Р¤РѕСЂРјР°С‚ (server_connection.cpp:1810):
    #   SpaceID(4) + vehicleID(4) + Position3D(3*float)
    #   + Direction3D(yaw,pitch,roll = 3*float) + cell_props.
    # cell_props (def-РїРѕСЂСЏРґРѕРє, Р»РёС€Рµ OWN_CLIENT РїРѕР»СЏ Avatar):
    #   team (UINT8) + playerVehicleID (OBJECT_ID=uint32).
    # Р’РђР–Р›РР’Рћ: playerVehicleID != 0 в†’ Avatar.onEnterWorld РІРёРєР»РёРєР°С”
    # set_playerVehicleID(0) (init step #1) + СЏРєС‰Рѕ Vehicle entity
    # PLAYER_VEHICLE_ID С–СЃРЅСѓС” С– inWorld вЂ” vehicle_onEnterWorld(own)
    # РїС–Р·РЅС–С€Рµ С‚РµР¶ РґР°С” init step #2. РўРѕРґС– Р· 4 РєСЂРѕРєС–РІ (1, 2, onEnterWorld,
    # onSpaceLoaded) Р·Р°РіСЂСѓР·РєР° Р·Р°РІРµСЂС€СѓС”С‚СЊСЃСЏ С– РїРѕС‡РёРЅР°С”С‚СЊСЃСЏ СЂРµР°Р»СЊРЅРёР№ Р±С–Р№.
    cell_props = b''
    team = max(1, min(2, int(team or 1)))
    cell_props += struct.pack('<B', team)              # team  (UINT8)
    cell_props += struct.pack('<I', PLAYER_VEHICLE_ID)  # playerVehicleID

    ccp = struct.pack('<I', SPACE_ID)
    ccp += struct.pack('<I', 0)               # vehicleID РЅР° СЏРєРѕРјСѓ СЃС‚РѕС—С‚СЊ Avatar вЂ” 0
    ccp += struct.pack('<fff', *spawn_pos)    # position (x, y, z)
    ccp += struct.pack('<fff', spawn_yaw, 0.0, 0.0) # direction (yaw, pitch, roll)
    ccp += cell_props
    msgs += msg_varlen(0x06, ccp)

    veh_info = (
        PLAYER_VEHICLE_ID, vehicle_compact_descr or get_vehicle_compact_descr(),
        player_name, team, True, False, False, 1, '', 0, 0)
    if vehicle_list is None:
        vehicle_list = [veh_info]
    if statistics_list is None:
        statistics_list = [(PLAYER_VEHICLE_ID, 0)]
    msgs += build_avatar_update_arena(ARENA_UPDATE_VEHICLE_LIST, vehicle_list)
    msgs += build_avatar_update_arena(ARENA_UPDATE_STATISTICS,
                                      statistics_list)
    msgs += build_avatar_update_arena(ARENA_UPDATE_PERIOD,
                                      (initial_period, period_end_time,
                                       period_length, None))

    # 4. enterAoI(Vehicle) Р”Рћ createEntity!
    #    EntityManager::onEntityCreate РїРµСЂРµРІС–СЂСЏС” unknownEntities_[id].count С–
    #    СЏРєС‰Рѕ С†Рµ 0 в†’ ERROR_MSG "didn't 'enter' before 'create'".
    #    РљРѕРјРµРЅС‚Р°СЂ Сѓ entity_manager.cpp:1869 РєР°Р¶Рµ:
    #      "we should call onEntityEnter before onEntityCreate, since the
    #       entities are not pre cached."
    #    Р¤РѕСЂРјР°С‚ (client_interface.hpp:93): EntityID(4) + IDAlias(uint8).
    msgs += msg_fixed(0x0A,
                      struct.pack('<IB', PLAYER_VEHICLE_ID, 0))

    # 5. createEntity(Vehicle) вЂ” СЃС‚РІРѕСЂСЋС” Vehicle entity Сѓ РєР»С–С”РЅС‚СЃСЊРєРѕРјСѓ
    #    entities map. Р¤РѕСЂРјР°С‚ (server_connection.cpp:1944, WoT 0.6.5 Р’РРљРћР РРЎРўРћР’РЈР„
    #    CompressionIStream вЂ” РїС–РґС‚РІРµСЂРґР¶РµРЅРѕ СЂСЏРґРєРѕРј
    #    "CompressionIStream::CompressionIStream: Invalid compression type: %d"
    #    Сѓ WorldOfTanks.exe). РўРѕРјСѓ payload РїРѕС‡РёРЅР°С”С‚СЊСЃСЏ Р· uint8 compressionType
    #    (0 = BW_COMPRESSION_NONE), С–РЅР°РєС€Рµ РєР»С–С”РЅС‚ РІРїР°РґРµ С‡РµСЂРµР· CRITICAL_MSG:
    #      uint8 compressionType (= 0 NONE)
    #      EntityID(4) + EntityTypeID(2) + Position3D(3*float)
    #      + yaw(int8) + pitch(int8) + roll(int8)
    #      + ALL_CLIENTS+CELL_PUBLIC properties (def order, pass 2).
    veh_compact_descr = vehicle_compact_descr or get_vehicle_compact_descr()
    msgs += build_vehicle_create_message(PLAYER_VEHICLE_ID,
                                         VEHICLE_ENTITY_TYPE,
                                         veh_compact_descr,
                                         vehicle_data=vehicle_data,
                                         pos=spawn_pos,
                                         yaw=spawn_yaw,
                                         team=team,
                                         player_name=player_name)
    return msgs


def _get_first_vehicle_compact_descr() -> bytes:
    """РџРѕРІРµСЂС‚Р°С” compactDescr РїРµСЂС€РѕРіРѕ С‚Р°РЅРєР° Р· _vehicles.json вЂ” С‰РѕР±
    Vehicle.publicInfo.compDescr РІС–РґРїРѕРІС–РґР°РІ СЂРµР°Р»СЊРЅРѕРјСѓ С‚Р°РЅРєСѓ РіСЂР°РІС†СЏ."""
    return get_vehicle_compact_descr()


def build_vehicle_create_message(vehicle_id: int, type_id: int,
                                 compact_descr: bytes,
                                 vehicle_data: dict = None,
                                 pos=(0.0, 0.0, 0.0),
                                 yaw: float = 0.0,
                                 team: int = 1,
                                 player_name: str = 'qwerty') -> bytes:
    """createEntity (msgID 0x08, varlen2). Р”РёРІРёСЃСЊ server_connection.cpp:1944.
    Property stream вЂ” С†Рµ 12 ALL_CLIENTS+CELL_PUBLIC РїРѕР»С–РІ Vehicle.def
    Сѓ def-РїРѕСЂСЏРґРєСѓ:
      1. publicInfo  (PUBLIC_VEHICLE_INFO FIXED_DICT: name, compDescr, team, prebattleID)
      2. health      (INT16)                ALL_CLIENTS
      3. isCrewActive(BOOL=UINT8)           ALL_CLIENTS
      4. engineMode  (ARRAY[2] UINT8)       ALL_CLIENTS
      5. damageStickers (ARRAY var UINT64)  ALL_CLIENTS
      6. publicStateModifiers (ARRAY var UINT8/EXTRA_ID)  ALL_CLIENTS
      7. status      (ARRAY[2] UINT8)       CELL_PUBLIC
      8. speeds      (ARRAY[2] FLOAT32)     CELL_PUBLIC
      9. invisibility(FLOAT32)              CELL_PUBLIC
     10. radioDistance(FLOAT32)             CELL_PUBLIC
     11. lastDamageTime(FLOAT64)            CELL_PUBLIC
     12. detectedVehicles(ARRAY var OBJECT_ID)  CELL_PUBLIC
    """
    # createEntity TAGGED stream (entity_type.cpp:427 newDictionary,
    # contents=TAGGED_CELL_ENTITY_DATA, allowOwnClientData=false):
    #   uint8 size
    #   { uint8 index + value-bytes } Г— size
    # index вЂ” С–РЅРґРµРєСЃ Сѓ СЃРїРёСЃРєСѓ clientServerProperty entity (С‚С–Р»СЊРєРё props
    # Р· ALL_CLIENTS / OTHER_CLIENTS РїСЂР°РїРѕСЂР°РјРё; CELL_PUBLIC С– OWN_CLIENT
    # С‚СѓС‚ РќР• РїСЂРёР№РјР°СЋС‚СЊСЃСЏ вЂ” Р»РёС€Рµ isOtherClientData=True).
    # Vehicle.def ALL_CLIENTS Сѓ def-РїРѕСЂСЏРґРєСѓ:
    #   idx=0 publicInfo (PUBLIC_VEHICLE_INFO FIXED_DICT)
    #   idx=1 health (INT16)
    #   idx=2 isCrewActive (BOOL)
    #   idx=3 engineMode (ARRAY[2] UINT8)
    #   idx=4 damageStickers (ARRAY var UINT64)
    #   idx=5 publicStateModifiers (ARRAY var UINT8)
    # Vehicle.prerequisites() РњРђР„ self.publicInfo.compDescr в†’ Р±РµР·
    # publicInfo Р±СѓРґРµ AttributeError Сѓ Python в†’ C++ crash. РўРѕРјСѓ С€Р»РµРјРѕ
    # С…РѕС‡Р° Р± publicInfo.

    # FIXED_DICT serialization: РїРѕР»СЏ Р±РµР· length prefix Сѓ def-order.
    # PUBLIC_VEHICLE_INFO = name(STRING) + compDescr(STRING) + team(UINT8)
    #                       + prebattleID(OBJECT_ID=uint32)
    public_info = b''
    public_info += bw_pack_string(player_name.encode('utf-8', 'ignore') or b'player')
    public_info += bw_pack_string(compact_descr)
    public_info += struct.pack('<B', team)
    public_info += struct.pack('<I', 0)       # prebattleID

    props = b''
    props += struct.pack('<B', 0)             # idx=0 (publicInfo)
    props += public_info
    props += struct.pack('<B', 1)             # idx=1 (health)
    props += struct.pack('<h', get_vehicle_max_health(vehicle_data))
    props += struct.pack('<B', 2)             # idx=2 (isCrewActive)
    props += struct.pack('<B', 1)
    props += struct.pack('<B', 3)             # idx=3 (engineMode)
    props += struct.pack('<BB', 1, 0)         # idle/started, no movement flags

    # CompressionIStream wrapper С‡РёС‚Р°С” 1-Р№ Р±Р°Р№С‚ СЏРє compression type.
    # 0 = BW_COMPRESSION_NONE (РґР°Р»С– raw stream Р±РµР· РґРµРєРѕРјРїСЂРµСЃС–С—).
    payload = struct.pack('<B', 0)            # compressionType = NONE
    payload += struct.pack('<I', vehicle_id)
    payload += struct.pack('<H', type_id)
    payload += struct.pack('<fff', *pos)
    payload += struct.pack('<bbb', angle_to_int8(yaw), 0, 0)   # yaw, pitch, roll
    payload += struct.pack('<B', 4)           # publicInfo, health, crew, engineMode
    payload += props
    return msg_varlen(0x08, payload)


def build_leave_aoi(entity_id: int) -> bytes:
    return msg_varlen(CLIENT_LEAVE_AOI_MSG_ID, struct.pack('<I', int(entity_id)))


def get_remote_vehicle_id(sess: dict) -> int:
    return 1000 + int(sess.get('account_id') or 0)

def get_display_team(observer_sess: dict, subject_sess: dict) -> int:
    subject_team = subject_sess.get('battle_team')
    if subject_team is None:
        return 1 if observer_sess is subject_sess else 2
    return max(1, min(2, int(subject_team)))


def get_battle_account_dbid(sess: dict) -> int:
    try:
        return max(1, int(sess.get('account_id') or 1))
    except (TypeError, ValueError):
        return 1


def build_battle_vehicle_info_for_viewer(viewer_sess: dict,
                                         subject_sess: dict):
    vehicle = get_session_battle_vehicle(subject_sess)
    veh_compact = (subject_sess.get('battle_vehicle_compactDescr') or
                   (vehicle or {}).get('compactDescr'))
    if not vehicle or not veh_compact:
        return None
    vehicle_id = (
        PLAYER_VEHICLE_ID
        if viewer_sess is subject_sess else get_remote_vehicle_id(subject_sess))
    health = subject_sess.get('battle_vehicle_health')
    if health is None:
        health = get_vehicle_max_health(vehicle)
    return (
        vehicle_id,
        veh_compact,
        subject_sess.get('username') or 'player',
        get_display_team(viewer_sess, subject_sess),
        int(health or 0) > 0,
        bool(subject_sess.get('battle_client_ready')),
        False,
        get_battle_account_dbid(subject_sess),
        '',
        0,
        0)


def get_viewer_roster_sessions(viewer_sess: dict):
    roster = viewer_sess.get('battle_roster_sessions')
    if roster:
        return list(roster)
    match_id = viewer_sess.get('battle_match_id')
    if match_id is None:
        return [viewer_sess]
    sessions = [
        sess for sess in active_battle_accounts.values()
        if sess.get('battle_match_id') == match_id and
        not sess.get('battle_ended')
    ]
    return sessions or [viewer_sess]


def build_match_vehicle_roster_for_viewer(viewer_sess: dict, sessions):
    match_id = viewer_sess.get('battle_match_id')
    roster = []
    seen = set()
    for subject_sess in sessions:
        if subject_sess is None:
            continue
        if match_id is not None and subject_sess.get('battle_match_id') != match_id:
            continue
        info = build_battle_vehicle_info_for_viewer(viewer_sess, subject_sess)
        if info is None or info[0] in seen:
            continue
        seen.add(info[0])
        roster.append(info)
    roster.sort(key=lambda info: (info[3], info[0]))
    return roster


def build_match_vehicle_statistics_for_viewer(viewer_sess: dict, sessions):
    stats = []
    seen = set()
    for info in build_match_vehicle_roster_for_viewer(viewer_sess, sessions):
        if info[0] in seen:
            continue
        seen.add(info[0])
        stats.append((info[0], 0))
    return stats


def remember_match_vehicle_roster(observer_sess: dict, sessions):
    accounts = observer_sess.setdefault('arena_remote_accounts', set())
    for subject_sess in sessions:
        if subject_sess is observer_sess or subject_sess is None:
            continue
        account_id = subject_sess.get('account_id')
        if account_id is not None:
            accounts.add(account_id)


def vehicle_spotting_class(vehicle: dict) -> str:
    vehicle = vehicle or {}
    if bool(vehicle.get('isSPG')):
        return 'SPG'
    if bool(vehicle.get('isATSPG')):
        return 'AT-SPG'
    vehicle_class = str(vehicle.get('vehicleClass') or '')
    if not vehicle_class:
        vehicle_class = vehicle_class_from_tags(normalize_vehicle_tags(vehicle))
    lower = vehicle_class.lower().replace('_', '-')
    if lower in ('light', 'lighttank'):
        return 'lightTank'
    if lower in ('medium', 'mediumtank'):
        return 'mediumTank'
    if lower in ('heavy', 'heavytank'):
        return 'heavyTank'
    if lower in ('at-spg', 'tankdestroyer', 'td'):
        return 'AT-SPG'
    if lower in ('spg', 'artillery'):
        return 'SPG'
    return vehicle_class or 'mediumTank'


def spotting_class_value(mapping: dict, vehicle_class: str, default):
    if not isinstance(mapping, dict):
        return default
    candidates = [
        vehicle_class,
        str(vehicle_class).lower(),
        str(vehicle_class).replace('-', '_'),
        str(vehicle_class).replace('_', '-'),
    ]
    for key in candidates:
        if key in mapping:
            return mapping[key]
    return default


def class_view_range_fallback(vehicle_class: str) -> float:
    return float(spotting_class_value(
        SPOTTING_VIEW_RANGE_FALLBACKS, vehicle_class, 330.0))


def class_view_range_limits(vehicle_class: str):
    values = spotting_class_value(
        SPOTTING_VIEW_RANGE_CLASS_LIMITS, vehicle_class, None)
    if isinstance(values, dict):
        low = float(values.get('min', 0.0))
        high = float(values.get('max', SPOTTING_MAX_RANGE))
    elif isinstance(values, (list, tuple)):
        low = float(values[0]) if len(values) >= 1 else 0.0
        high = float(values[1]) if len(values) >= 2 else SPOTTING_MAX_RANGE
    elif values is not None:
        low = high = float(values)
    else:
        low, high = 0.0, SPOTTING_MAX_RANGE
    if high < low:
        low, high = high, low
    return max(0.0, low), max(0.0, high)


def class_camo_fallback(vehicle_class: str):
    values = spotting_class_value(
        SPOTTING_CAMO_FALLBACKS, vehicle_class, (0.12, 0.17))
    if isinstance(values, (list, tuple)):
        moving = float(values[0]) if len(values) >= 1 else 0.12
        still = float(values[1]) if len(values) >= 2 else moving
    else:
        moving = still = float(values)
    return moving, still


def class_camo_multiplier(vehicle_class: str) -> float:
    return float(spotting_class_value(
        SPOTTING_CAMO_CLASS_MULTIPLIERS, vehicle_class, 1.0))


def vehicle_view_range(vehicle: dict) -> float:
    vehicle = vehicle or {}
    vehicle_class = vehicle_spotting_class(vehicle)
    value = float(vehicle.get('circularVisionRadius') or 0.0)
    if value <= 0.0:
        value = class_view_range_fallback(vehicle_class)
    low, high = class_view_range_limits(vehicle_class)
    return clamp(max(0.0, value), low, high)


def vehicle_base_invisibility(vehicle: dict, moving: bool) -> float:
    vehicle = vehicle or {}
    vehicle_class = vehicle_spotting_class(vehicle)
    fallback_moving, fallback_still = class_camo_fallback(vehicle_class)
    key = 'invisibilityMoving' if moving else 'invisibilityStill'
    fallback = fallback_moving if moving else fallback_still
    value = float(vehicle.get(key) or 0.0)
    if value <= 0.0:
        value = fallback
    value *= class_camo_multiplier(vehicle_class)
    return clamp(value, 0.0, 0.95)


def session_observed_speed(sess: dict) -> float:
    values = [
        sess.get('battle_speed', 0.0),
        sess.get('battle_target_speed', 0.0),
        sess.get('client_vehicle_observed_speed', 0.0),
    ]
    speeds = []
    for value in values:
        try:
            speeds.append(abs(float(value)))
        except (TypeError, ValueError):
            continue
    return max(speeds) if speeds else 0.0


def is_same_battle_team(observer_sess: dict, source_sess: dict) -> bool:
    if observer_sess is source_sess:
        return True
    observer_team = observer_sess.get('battle_team')
    source_team = source_sess.get('battle_team')
    return observer_team is not None and observer_team == source_team


def spotting_applies(observer_sess: dict, source_sess: dict) -> bool:
    if not SPOTTING_ENABLED:
        return False
    if observer_sess is source_sess:
        return False
    if observer_sess.get('battle_match_id') != source_sess.get('battle_match_id'):
        return False
    if is_same_battle_team(observer_sess, source_sess):
        return False
    if not observer_sess.get('battle_period_active'):
        return False
    if not source_sess.get('battle_period_active'):
        return False
    return True


def spotting_bush_bonus_between(observer_sess: dict, source_sess: dict,
                                observer_pos, source_pos) -> float:
    if SPOTTING_BUSH_BONUS <= 0.0 or SPOTTING_BUSH_MAX_BONUS <= 0.0:
        return 0.0
    arena_type_id = normalize_arena_type_id(
        source_sess.get('battle_arena_type_id') or
        observer_sess.get('battle_arena_type_id') or
        ARENA_TYPE_KARELIA)
    ax, az = float(observer_pos[0]), float(observer_pos[2])
    bx, bz = float(source_pos[0]), float(source_pos[2])
    bonus = 0.0
    for bush in iter_spotting_bushes_near_segment(
            arena_type_id, ax, az, bx, bz, halo=SPOTTING_BUSH_RADIUS):
        x, z, radius, _path = bush
        if point_segment_distance_sq_xz(
                float(x), float(z), ax, az, bx, bz) <= float(radius) * float(radius):
            bonus += SPOTTING_BUSH_BONUS
            if bonus >= SPOTTING_BUSH_MAX_BONUS:
                return SPOTTING_BUSH_MAX_BONUS
    return clamp(bonus, 0.0, SPOTTING_BUSH_MAX_BONUS)


def vehicle_current_invisibility(source_sess: dict, observer_sess: dict,
                                 observer_pos, source_pos) -> float:
    vehicle = get_session_battle_vehicle(source_sess)
    moving = session_observed_speed(source_sess) > SPOTTING_STATIONARY_SPEED
    camo = vehicle_base_invisibility(vehicle, moving) * SPOTTING_CAMO_SCALE
    shot_time = float(source_sess.get('battle_last_spotting_shot_time') or 0.0)
    if shot_time > 0.0 and time.time() - shot_time <= SPOTTING_SHOT_CAMO_PENALTY_SECONDS:
        factor = float((vehicle or {}).get('gunInvisibilityFactorAtShot') or 0.25)
        camo *= clamp(factor, 0.0, 1.0)
    camo += spotting_bush_bonus_between(observer_sess, source_sess,
                                        observer_pos, source_pos)
    return clamp(camo, 0.0, 0.95)


def is_live_vehicle_session(sess: dict) -> bool:
    if sess.get('battle_ended'):
        return False
    if 'battle_vehicle_health' in sess:
        return int(sess.get('battle_vehicle_health') or 0) > 0
    return True


def is_destroyed_vehicle_session(sess: dict) -> bool:
    return 'battle_vehicle_health' in sess and int(sess.get('battle_vehicle_health') or 0) <= 0


def freeze_destroyed_vehicle(sess: dict) -> bool:
    if not is_destroyed_vehicle_session(sess):
        return False
    sess['battle_motion_flags'] = 0
    sess['battle_speed'] = 0.0
    sess['battle_rspeed'] = 0.0
    sess['battle_target_speed'] = 0.0
    sess['battle_target_rspeed'] = 0.0
    sess['client_vehicle_observed_speed'] = 0.0
    sess['client_vehicle_observed_rspeed'] = 0.0
    sess['client_vehicle_pos'] = None
    sess['client_vehicle_last_update_time'] = 0.0
    sess['server_vehicle_authoritative'] = True
    sess['battle_motion_force_position'] = True
    return True


_BATTLE_TICK_CONTEXT = None


def begin_battle_tick_context(sessions):
    global _BATTLE_TICK_CONTEXT
    team_live_sessions = {}
    for sess in sessions:
        if not sess.get('battle_bundle_sent'):
            continue
        if not sess.get('battle_period_active'):
            continue
        if not is_live_vehicle_session(sess):
            continue
        key = (sess.get('battle_match_id'), sess.get('battle_team'))
        team_live_sessions.setdefault(key, []).append(sess)
    _BATTLE_TICK_CONTEXT = {
        'team_live_sessions': team_live_sessions,
        'direct_visibility': {},
        'vehicle_visibility': {},
    }


def end_battle_tick_context():
    global _BATTLE_TICK_CONTEXT
    _BATTLE_TICK_CONTEXT = None


def battle_tick_context_cache(name):
    if not isinstance(_BATTLE_TICK_CONTEXT, dict):
        return None
    return _BATTLE_TICK_CONTEXT.get(name)


def is_vehicle_directly_visible_to(observer_sess: dict, source_sess: dict) -> bool:
    cache = battle_tick_context_cache('direct_visibility')
    cache_key = None
    if cache is not None:
        cache_key = (id(observer_sess), id(source_sess))
        if cache_key in cache:
            return cache[cache_key]
    should_mark = False
    if observer_sess is source_sess:
        visible = True
    elif observer_sess.get('battle_match_id') != source_sess.get('battle_match_id'):
        visible = False
    elif not SPOTTING_ENABLED:
        visible = True
    elif is_same_battle_team(observer_sess, source_sess):
        visible = True
    elif (not observer_sess.get('battle_period_active') or
            not source_sess.get('battle_period_active')):
        visible = False
    else:
        observer_pos = safe_vec3(get_effective_vehicle_pos(observer_sess, None), None)
        source_pos = safe_vec3(get_effective_vehicle_pos(source_sess, None), None)
        if observer_pos is None or source_pos is None:
            visible = False
        else:
            dx = float(source_pos[0]) - float(observer_pos[0])
            dz = float(source_pos[2]) - float(observer_pos[2])
            distance = math.sqrt(dx * dx + dz * dz)
            auto_reveal = max(0.0, SPOTTING_AUTO_REVEAL_DISTANCE)
            max_range = max(auto_reveal, SPOTTING_MAX_RANGE)
            if distance <= auto_reveal:
                visible = True
                should_mark = True
            else:
                view_range = clamp(
                    vehicle_view_range(get_session_battle_vehicle(observer_sess)),
                    auto_reveal, max_range)
                camo = vehicle_current_invisibility(source_sess, observer_sess,
                                                    observer_pos, source_pos)
                spot_range = view_range - (view_range - auto_reveal) * camo
                spot_range = clamp(spot_range, auto_reveal, max_range)
                visible = distance <= spot_range
                should_mark = visible
    if should_mark:
        mark_remote_vehicle_spotted(observer_sess, source_sess)
    if cache is not None:
        cache[cache_key] = visible
    return visible


def is_vehicle_spotted_by_observer_team(observer_sess: dict, source_sess: dict) -> bool:
    if not spotting_applies(observer_sess, source_sess):
        return False
    observer_team = observer_sess.get('battle_team')
    if observer_team is None:
        return False
    match_id = observer_sess.get('battle_match_id')
    ctx = _BATTLE_TICK_CONTEXT
    if isinstance(ctx, dict):
        spotters = ctx.get('team_live_sessions', {}).get(
            (match_id, observer_team), ())
    else:
        with battle_lock:
            spotters = [
                sess for sess in active_battle_accounts.values()
                if sess.get('battle_match_id') == match_id and
                sess.get('battle_team') == observer_team and
                sess.get('battle_bundle_sent') and
                sess.get('battle_period_active') and
                is_live_vehicle_session(sess)
            ]
    for spotter in spotters:
        if spotter is observer_sess or spotter is source_sess:
            continue
        if is_vehicle_directly_visible_to(spotter, source_sess):
            return True
    return False


def is_vehicle_visible_to(observer_sess: dict, source_sess: dict) -> bool:
    cache = battle_tick_context_cache('vehicle_visibility')
    cache_key = None
    if cache is not None:
        cache_key = (id(observer_sess), id(source_sess))
        if cache_key in cache:
            return cache[cache_key]
    visible = (
        is_vehicle_directly_visible_to(observer_sess, source_sess) or
        is_vehicle_spotted_by_observer_team(observer_sess, source_sess)
    )
    if cache is not None:
        cache[cache_key] = visible
    return visible


def mark_remote_vehicle_spotted(observer_sess: dict, source_sess: dict):
    account_id = source_sess.get('account_id')
    if not account_id or is_same_battle_team(observer_sess, source_sess):
        return
    observer_sess.setdefault('battle_spotted_vehicle_ids', set()).add(account_id)


def remember_remote_vehicle_in_arena(observer_sess: dict, remote_sess: dict):
    account_id = remote_sess.get('account_id')
    if account_id:
        observer_sess.setdefault('arena_remote_accounts', set()).add(account_id)


def mark_remote_vehicle_intro_sent(observer_sess: dict, remote_sess: dict):
    account_id = remote_sess.get('account_id')
    if account_id is None:
        return
    observer_sess.setdefault('remote_vehicle_intro_times', {})[account_id] = time.time()


def clear_remote_vehicle_intro(observer_sess: dict, remote_sess: dict):
    account_id = remote_sess.get('account_id')
    intro_times = observer_sess.get('remote_vehicle_intro_times')
    if account_id is None or not isinstance(intro_times, dict):
        return
    intro_times.pop(account_id, None)


def remote_vehicle_intro_remaining_delay(observer_sess: dict,
                                         remote_sess: dict) -> float:
    account_id = remote_sess.get('account_id')
    intro_times = observer_sess.get('remote_vehicle_intro_times')
    if account_id is None or not isinstance(intro_times, dict):
        return 0.0
    sent_at = intro_times.get(account_id)
    if sent_at is None:
        return 0.0
    age = time.time() - safe_float(sent_at, 0.0)
    return max(0.0, REMOTE_ENTITY_INTRO_GRACE_SECONDS - age)


def client_arena_vehicle_ids(observer_sess: dict):
    vehicle_ids = {PLAYER_VEHICLE_ID}
    accounts = set(observer_sess.get('arena_remote_accounts', set()))
    accounts.update(observer_sess.get('known_remote_accounts', set()))
    for account_id in accounts:
        try:
            vehicle_ids.add(get_remote_vehicle_id({'account_id': int(account_id)}))
        except (TypeError, ValueError):
            continue
    return sorted(vehicle_ids)


def build_minimap_positions_update(observer_sess: dict, sessions) -> bytes:
    ids = client_arena_vehicle_ids(observer_sess)
    index_by_id = {vehicle_id: index for index, vehicle_id in enumerate(ids)}
    indices = []
    positions = []
    seen = set()
    for sess in sessions:
        if sess.get('battle_match_id') != observer_sess.get('battle_match_id'):
            continue
        if sess is observer_sess:
            vehicle_id = PLAYER_VEHICLE_ID
        else:
            account_id = sess.get('account_id')
            if account_id not in observer_sess.setdefault('known_remote_accounts', set()):
                continue
            if not is_vehicle_visible_to(observer_sess, sess):
                continue
            vehicle_id = get_remote_vehicle_id(sess)
        if vehicle_id in seen or vehicle_id not in index_by_id:
            continue
        pos = safe_vec3(get_effective_vehicle_pos(sess, None), None)
        if pos is None:
            continue
        seen.add(vehicle_id)
        indices.append(index_by_id[vehicle_id])
        positions.append(int(round(clamp(float(pos[0]), -32768.0, 32767.0))))
        positions.append(int(round(clamp(float(pos[2]), -32768.0, 32767.0))))
    return build_avatar_update_positions(indices, positions)


def mark_vehicle_shot_visibility_penalty(sess: dict):
    now = time.time()
    sess['battle_last_shot_time'] = now
    sess['battle_last_spotting_shot_time'] = now


def shot_visibility_grace_active(observer_sess: dict, source_sess: dict) -> bool:
    if observer_sess is source_sess:
        return False
    if SHOT_VISIBILITY_GRACE_SECONDS <= 0.0:
        return False
    account_id = source_sess.get('account_id')
    if account_id not in observer_sess.setdefault('known_remote_accounts', set()):
        return False
    last = safe_float(source_sess.get('battle_last_shot_time'), 0.0)
    return last > 0.0 and time.time() - last <= SHOT_VISIBILITY_GRACE_SECONDS


def keep_remote_entity_on_visibility_loss(observer_sess: dict,
                                          remote_sess: dict) -> bool:
    if not KEEP_REMOTE_ENTITIES_ON_VISIBILITY_LOSS:
        return False
    if observer_sess is remote_sess:
        return False
    if observer_sess.get('battle_match_id') != remote_sess.get('battle_match_id'):
        return False
    if not observer_sess.get('battle_period_active'):
        return False
    if not remote_sess.get('battle_period_active'):
        return False
    if is_destroyed_vehicle_session(remote_sess):
        return False
    account_id = remote_sess.get('account_id')
    return account_id in observer_sess.setdefault('known_remote_accounts', set())


def remember_shot_visual_viewer(source_sess: dict, shot_id: int,
                                viewer_sess: dict):
    account_id = viewer_sess.get('account_id')
    if account_id is None:
        return
    ensure_shot_visual_viewers(source_sess, shot_id).add(account_id)


def ensure_shot_visual_viewers(source_sess: dict, shot_id: int):
    shots = source_sess.setdefault('battle_visual_shot_viewers', {})
    return shots.setdefault(int(shot_id), set())


def pop_shot_visual_viewers(source_sess: dict, shot_id: int):
    shots = source_sess.get('battle_visual_shot_viewers')
    if not isinstance(shots, dict):
        return None
    return shots.pop(int(shot_id), None)


def hide_remote_vehicle(sock, observer_sess: dict, remote_sess: dict):
    remote_account_id = remote_sess.get('account_id')
    known = observer_sess.setdefault('known_remote_accounts', set())
    if not remote_account_id or remote_account_id not in known:
        return False
    if remote_vehicle_intro_remaining_delay(observer_sess, remote_sess) > 0.0:
        return False
    observer_addr = observer_sess.get('addr')
    if observer_addr:
        remote_id = get_remote_vehicle_id(remote_sess)
        send_avatar_messages(sock, observer_addr, observer_sess,
                             build_leave_aoi(remote_id),
                             f"leaveAoI(Vehicle#{remote_id})",
                             reliable=True)
    known.discard(remote_account_id)
    clear_remote_vehicle_intro(observer_sess, remote_sess)
    return False


def update_remote_vehicle_visibility(sock, observer_sess: dict,
                                     remote_sess: dict) -> bool:
    if observer_sess is remote_sess:
        return True
    if not is_vehicle_visible_to(observer_sess, remote_sess):
        if shot_visibility_grace_active(observer_sess, remote_sess):
            return False
        if keep_remote_entity_on_visibility_loss(observer_sess, remote_sess):
            return False
        hide_remote_vehicle(sock, observer_sess, remote_sess)
        return False
    known = observer_sess.setdefault('known_remote_accounts', set())
    remote_account_id = remote_sess.get('account_id')
    if remote_account_id and remote_account_id not in known:
        send_remote_vehicle(sock, observer_sess, remote_sess)
    return True


def is_recent_client_vehicle_position(sess: dict) -> bool:
    if sess.get('client_vehicle_pos') is None:
        return False
    last = float(sess.get('client_vehicle_last_update_time', 0.0))
    return time.time() - last <= CLIENT_POSITION_MAX_AGE


def get_effective_vehicle_pos(sess: dict, fallback=None):
    if not sess.get('server_vehicle_authoritative', True) and is_recent_client_vehicle_position(sess):
        return sess.get('client_vehicle_pos')
    pos = sess.get('battle_pos')
    if pos is not None:
        return pos
    if fallback is not None:
        return fallback
    return ARENA_SPAWN_POS[ARENA_TYPE_KARELIA]


def record_client_vehicle_position(sess: dict, pos, yaw: float):
    if freeze_destroyed_vehicle(sess):
        return
    prev = sess.get('client_vehicle_pos')
    prev_time = float(sess.get('client_vehicle_last_update_time', 0.0))
    now = time.time()
    motion_prev = prev if prev is not None else sess.get('battle_pos')
    if motion_prev is not None:
        prev_x = float(motion_prev[0])
        prev_z = float(motion_prev[2])
        new_x, new_z, blocked = resolve_motion_against_vehicles(
            sess, prev_x, prev_z,
            float(pos[0]), float(pos[2]), yaw=yaw)
        arena_type_id = normalize_arena_type_id(sess.get('battle_arena_type_id'))
        new_x, new_z, static_blocked = resolve_motion_against_obstacles(
            arena_type_id, prev_x, prev_z, new_x, new_z)
        if static_blocked:
            resolved_y, _normal = sample_terrain(
                arena_type_id, new_x, new_z, float(pos[1]))
            sess['battle_motion_force_position'] = True
            pos = (new_x, resolved_y, new_z)
        elif blocked:
            pos = (new_x, float(pos[1]), new_z)
    if BATTLE_VERBOSE_DEBUG and prev is None:
        print(f"    [client_pos] FIRST set client_vehicle_pos=({pos[0]:.1f},{pos[1]:.1f},{pos[2]:.1f}) yaw={yaw:.2f}")
    if prev is not None:
        dx = pos[0] - prev[0]
        dy = pos[1] - prev[1]
        dz = pos[2] - prev[2]
        if dx * dx + dy * dy + dz * dz > 0.0001:
            sess['client_vehicle_last_move_time'] = now
        xz_dist = math.sqrt(dx * dx + dz * dz)
        if xz_dist > TANK_SLOPE_SAMPLE_MIN_XZ:
            slope = dy / xz_dist
            prev_slope = float(sess.get('client_vehicle_slope', 0.0))
            new_slope = (
                prev_slope * TANK_SLOPE_SMOOTHING +
                slope * (1.0 - TANK_SLOPE_SMOOTHING))
            sess['client_vehicle_slope'] = new_slope
            sess['client_vehicle_slope_time'] = now
            sample_count = sess.get('client_vehicle_slope_sample_count', 0) + 1
            sess['client_vehicle_slope_sample_count'] = sample_count
            if BATTLE_VERBOSE_DEBUG:
                print(f"    [slope] sample dy={dy:+.2f} dxz={xz_dist:.2f} "
                      f"raw={slope:+.3f} EMA={prev_slope:+.3f}->{new_slope:+.3f} "
                      f"client_y={pos[1]:.2f}")
        dt = max(0.001, min(1.0, now - prev_time)) if prev_time > 0.0 else 0.0
        if dt > 0.0:
            forward = dx * math.sin(yaw) + dz * math.cos(yaw)
            sess['client_vehicle_observed_speed'] = forward / dt
            sess['client_vehicle_observed_rspeed'] = normalize_angle(
                yaw - float(sess.get('client_vehicle_yaw', yaw))) / dt
        else:
            sess['client_vehicle_observed_speed'] = 0.0
            sess['client_vehicle_observed_rspeed'] = 0.0
    else:
        sess['client_vehicle_observed_speed'] = 0.0
        sess['client_vehicle_observed_rspeed'] = 0.0
    if not sess.get('server_vehicle_authoritative', True):
        sess['battle_prev_pos'] = sess.get('battle_pos', pos)
        sess['battle_pos'] = pos
        sess['battle_yaw'] = yaw
        sess['battle_speed'] = float(sess.get('client_vehicle_observed_speed', 0.0))
        sess['battle_rspeed'] = float(sess.get('client_vehicle_observed_rspeed', 0.0))
    if is_hull_locked_gun_vehicle(get_session_battle_vehicle(sess)):
        sess['battle_turret_yaw'] = yaw
    sess['client_vehicle_pos'] = pos
    sess['client_vehicle_yaw'] = yaw
    sess['client_vehicle_last_update_time'] = now


def is_plausible_avatar_vehicle_position(sess: dict, pos) -> bool:
    base = sess.get('client_vehicle_pos') or sess.get('battle_pos')
    if base is None:
        return True
    dx = float(pos[0]) - float(base[0])
    dy = float(pos[1]) - float(base[1])
    dz = float(pos[2]) - float(base[2])
    return dx * dx + dy * dy + dz * dz <= CLIENT_AVATAR_VEHICLE_POS_MAX_DELTA * CLIENT_AVATAR_VEHICLE_POS_MAX_DELTA


def get_remote_vehicle_angles(remote_sess: dict):
    if (not remote_sess.get('server_vehicle_authoritative', True) and
            is_recent_client_vehicle_position(remote_sess)):
        yaw = float(remote_sess.get('client_vehicle_yaw', remote_sess.get('battle_yaw', 0.0)))
    else:
        yaw = float(remote_sess.get('battle_yaw', 0.0))
    vehicle = get_session_battle_vehicle(remote_sess)
    turret_yaw = (
        yaw if is_hull_locked_gun_vehicle(vehicle)
        else float(remote_sess.get('battle_turret_yaw', yaw)))
    gun_pitch = -float(remote_sess.get('battle_gun_pitch', 0.0))
    gun_pitch = clamp(gun_pitch * REMOTE_GUN_PITCH_SCALE,
                      -REMOTE_GUN_PITCH_LIMIT,
                      REMOTE_GUN_PITCH_LIMIT)
    return (
        yaw,
        gun_pitch,
        normalize_angle(turret_yaw - yaw),
    )


def build_remote_vehicle_messages(observer_sess: dict, remote_sess: dict) -> bytes:
    remote_vehicle = get_session_battle_vehicle(remote_sess)
    veh_compact = (remote_sess.get('battle_vehicle_compactDescr') or
                   (remote_vehicle or {}).get('compactDescr'))
    if not remote_vehicle or not veh_compact:
        print(f"    [!] remote vehicle missing for "
              f"{remote_sess.get('username') or 'player'} "
              f"invID={remote_sess.get('battle_vehicle_inv_id')}")
        return b''
    remote_id = get_remote_vehicle_id(remote_sess)
    remote_name = remote_sess.get('username') or 'player'
    remote_pos = get_effective_vehicle_pos(
        remote_sess, ARENA_SPAWN_POS[ARENA_TYPE_KARELIA])
    remote_yaw, remote_gun_pitch, remote_turret_yaw = get_remote_vehicle_angles(remote_sess)
    display_team = get_display_team(observer_sess, remote_sess)
    veh_info = (
        remote_id, veh_compact, remote_name, display_team,
        True, bool(remote_sess.get('battle_client_ready')), False,
        get_battle_account_dbid(remote_sess), '', 0, 0)
    msgs = b''
    if remote_sess.get('account_id') not in observer_sess.get('arena_remote_accounts', set()):
        msgs += build_avatar_update_arena(ARENA_UPDATE_VEHICLE_ADDED, veh_info)
        msgs += build_avatar_update_arena(ARENA_UPDATE_VEHICLE_STATISTICS, (remote_id, 0))
    msgs += msg_fixed(0x0A, struct.pack('<IB', remote_id, 0))
    msgs += build_vehicle_create_message(remote_id,
                                         VEHICLE_ENTITY_TYPE,
                                         veh_compact,
                                         vehicle_data=remote_vehicle,
                                         pos=remote_pos,
                                         yaw=remote_yaw,
                                         team=display_team,
                                         player_name=remote_name)
    msgs += build_vehicle_motion_update_for(remote_id, remote_pos, remote_yaw,
                                            remote_gun_pitch, remote_turret_yaw)
    return msgs

def send_remote_vehicle(sock, observer_sess: dict, remote_sess: dict):
    observer_addr = observer_sess.get('addr')
    if not observer_addr:
        return
    if not observer_sess.get('battle_bundle_sent') or not remote_sess.get('battle_bundle_sent'):
        return
    if observer_sess is remote_sess:
        return
    if not is_vehicle_visible_to(observer_sess, remote_sess):
        return
    known = observer_sess.setdefault('known_remote_accounts', set())
    remote_account_id = remote_sess.get('account_id')
    if not remote_account_id or remote_account_id in known:
        return
    msgs = build_remote_vehicle_messages(observer_sess, remote_sess)
    if not msgs:
        return
    remote_vehicle = get_session_battle_vehicle(remote_sess)
    fwd, _bwd = get_vehicle_speed_limits(remote_vehicle)
    if send_avatar_messages(sock, observer_addr, observer_sess,
                            msgs,
                            f"remote Vehicle#{get_remote_vehicle_id(remote_sess)} "
                            f"({remote_sess.get('username') or 'player'}, "
                            f"tank={(remote_vehicle or {}).get('name', 'unknown')}, "
                            f"fwd={fwd * 3.6:.1f}km/h)",
                            reliable=True):
        known.add(remote_account_id)
        mark_remote_vehicle_intro_sent(observer_sess, remote_sess)
        remember_remote_vehicle_in_arena(observer_sess, remote_sess)
        mark_remote_vehicle_spotted(observer_sess, remote_sess)

def announce_battle_player(sock, sess: dict):
    with battle_lock:
        active_battle_accounts[sess.get('account_id')] = sess
        others = [other for account_id, other in active_battle_accounts.items()
                  if account_id != sess.get('account_id') and
                  other.get('battle_match_id') == sess.get('battle_match_id')]
    broadcast_account_server_counters(sock)
    for other in others:
        send_remote_vehicle(sock, sess, other)
        send_remote_vehicle(sock, other, sess)

def broadcast_remote_vehicle_position(sock, source_sess: dict, force: bool = False):
    if not source_sess.get('battle_bundle_sent'):
        return
    now = time.time()
    if not force and now - float(source_sess.get('last_remote_broadcast', 0.0)) < 0.05:
        return
    source_sess['last_remote_broadcast'] = now
    source_account_id = source_sess.get('account_id')
    remote_id = get_remote_vehicle_id(source_sess)
    pos = get_effective_vehicle_pos(
        source_sess, ARENA_SPAWN_POS[ARENA_TYPE_KARELIA])
    yaw, gun_pitch, turret_yaw = get_remote_vehicle_angles(source_sess)
    update_msg = build_vehicle_motion_update_for(remote_id, pos, yaw,
                                                 gun_pitch, turret_yaw)
    with battle_lock:
        observers = [other for account_id, other in active_battle_accounts.items()
                     if account_id != source_account_id and
                     other.get('battle_match_id') == source_sess.get('battle_match_id')]
    for observer in observers:
        if not observer.get('addr'):
            continue
        known = observer.setdefault('known_remote_accounts', set())
        if not update_remote_vehicle_visibility(sock, observer, source_sess):
            continue
        if source_account_id not in known:
            continue
        send_avatar_messages(sock, observer.get('addr'), observer,
                             update_msg,
                             '',
                             reliable=False)

def start_battle_tick_loop(sock):
    global battle_tick_started, battle_tick_sock
    if SERVER_RUNTIME is not None:
        SERVER_RUNTIME.start_battle_tick(sock)
        battle_tick_sock = sock
        battle_tick_started = True
        return
    battle_tick_sock = sock
    if battle_tick_started:
        return
    battle_tick_started = True

    def _loop():
        next_tick = time.time()
        last_tick_dbg = time.time()
        tick_count = 0
        last_iter_count = 0
        max_iter_ms = 0.0
        while True:
            try:
                iter_start = time.time()
                next_tick += BATTLE_MOTION_TICK
                delay = next_tick - time.time()
                if delay > 0:
                    time.sleep(delay)
                else:
                    next_tick = time.time()
                with battle_lock:
                    sessions = [
                        sess for sess in active_battle_accounts.values()
                        if sess.get('battle_bundle_sent') and sess.get('battle_period_active')
                    ]
                if not sessions:
                    continue
                tick_count += 1
                remote_payloads = []
                def payload_for(observer):
                    payload = None
                    for entry in remote_payloads:
                        if entry[0] is observer:
                            payload = entry[1]
                            break
                    if payload is None:
                        payload = []
                        remote_payloads.append((observer, payload))
                    return payload

                for sess in sessions:
                    flags = sess.get('battle_motion_flags', 0)
                    if sess.get('server_vehicle_authoritative', True):
                        pos, yaw, speed, rspeed = advance_battle_motion(sess, flags)
                        addr = sess.get('addr')
                        if addr:
                            send_avatar_messages(sock, addr, sess,
                                                 build_battle_motion_tick_for_session(
                                                     sess, pos, yaw, speed, rspeed),
                                                 '',
                                                 reliable=False)
                    else:
                        pos = get_effective_vehicle_pos(
                            sess, sess.get('battle_pos',
                                           ARENA_SPAWN_POS[ARENA_TYPE_KARELIA]))
                        yaw = float(sess.get('client_vehicle_yaw',
                                             sess.get('battle_yaw', 0.0)))
                        if sess.pop('battle_motion_force_position', False):
                            addr = sess.get('addr')
                            if addr:
                                send_avatar_messages(
                                    sock, addr, sess,
                                    build_forced_position(
                                        PLAYER_VEHICLE_ID, pos, yaw,
                                        space_id=SPACE_ID, vehicle_id=0),
                                    '',
                                    reliable=False)
                    process_pending_vehicle_collision(sock, sess)
                    source_account_id = sess.get('account_id')
                    remote_id = get_remote_vehicle_id(sess)
                    _yaw, gun_pitch, turret_yaw = get_remote_vehicle_angles(sess)
                    remote_pos = get_effective_vehicle_pos(sess, pos)
                    if (not sess.get('server_vehicle_authoritative', True) and
                            is_recent_client_vehicle_position(sess)):
                        yaw = float(sess.get('client_vehicle_yaw', yaw))
                    remote_msg = build_vehicle_motion_update_for(remote_id, remote_pos, yaw,
                                                                 gun_pitch, turret_yaw)
                    for observer in sessions:
                        if observer is sess or not observer.get('addr'):
                            continue
                        if observer.get('battle_match_id') != sess.get('battle_match_id'):
                            continue
                        known = observer.setdefault('known_remote_accounts', set())
                        if not update_remote_vehicle_visibility(sock, observer, sess):
                            continue
                        if source_account_id not in known:
                            continue
                        payload_for(observer).append(remote_msg)
                for observer in sessions:
                    if not observer.get('addr'):
                        continue
                    msg = build_minimap_positions_update(observer, sessions)
                    if msg:
                        payload_for(observer).append(msg)
                for observer, payload in remote_payloads:
                    send_avatar_messages(sock, observer.get('addr'), observer,
                                         b''.join(payload), '', reliable=False)
                processed_matches = set()
                for sess in sessions:
                    match_id = sess.get('battle_match_id')
                    if match_id in processed_matches:
                        continue
                    processed_matches.add(match_id)
                    process_base_capture(sock, match_id)
                iter_ms = (time.time() - iter_start) * 1000.0
                if iter_ms > max_iter_ms:
                    max_iter_ms = iter_ms
                now_dbg = time.time()
                if now_dbg - last_tick_dbg >= 5.0:
                    elapsed = now_dbg - last_tick_dbg
                    ticks_done = tick_count - last_iter_count
                    actual_hz = ticks_done / max(0.001, elapsed)
                    print(f"    [tick] sessions={len(sessions)} "
                          f"actualHz={actual_hz:.1f} "
                          f"(target={1.0 / BATTLE_MOTION_TICK:.0f}) "
                          f"maxIterMs={max_iter_ms:.1f}")
                    last_tick_dbg = now_dbg
                    last_iter_count = tick_count
                    max_iter_ms = 0.0
            except Exception as exc:
                print(f"[!] Battle tick loop error: {exc}")
                time.sleep(0.1)

    thread = threading.Thread(target=_loop, daemon=True)
    thread.start()
    print(f"[*] Battle tick loop started ({1.0 / BATTLE_MOTION_TICK:.0f} Hz)")


def get_spawn_base_spawns(arena_type_id: int, base_id: int):
    team_spawns = ARENA_TEAM_SPAWNS.get(int(arena_type_id), {})
    spawns = team_spawns.get(int(base_id))
    if spawns:
        return spawns
    pos = ARENA_SPAWN_POS.get(arena_type_id, ARENA_SPAWN_POS[ARENA_TYPE_KARELIA])
    if int(base_id) == 2:
        return [(-pos[0], pos[1], -pos[2])]
    return [pos]


def get_session_spawn_base(sess: dict) -> int:
    bases = sess.get('battle_base_assignment') or {}
    return int(bases.get(sess.get('battle_team'), 1))


def spawn_yaw_for_base(base_id: int) -> float:
    return math.pi if int(base_id) == 2 else 0.0


def pick_spawn_pos(arena_type_id: int, sess: dict):
    base_id = get_session_spawn_base(sess)
    spawns = get_spawn_base_spawns(arena_type_id, base_id)
    spawn_index = int(sess.get('battle_spawn_index') or 0)
    return spawns[spawn_index % len(spawns)]


def get_capture_base_position(arena_type_id: int, base_id: int):
    base_map = ARENA_CAPTURE_BASE_POSITIONS.get(int(arena_type_id), {})
    pos = base_map.get(int(base_id))
    if pos is not None:
        return pos
    spawns = get_spawn_base_spawns(arena_type_id, base_id)
    if not spawns:
        fallback = ARENA_SPAWN_POS.get(arena_type_id,
                                       ARENA_SPAWN_POS[ARENA_TYPE_KARELIA])
        return fallback[0], fallback[2]
    x = sum(float(pos[0]) for pos in spawns) / len(spawns)
    z = sum(float(pos[2]) for pos in spawns) / len(spawns)
    return x, z


def build_battle_capture_state(arena_type_id: int, base_assignment: dict):
    bases = {}
    for team in (1, 2):
        base_id = int((base_assignment or {}).get(team, team))
        x, z = get_capture_base_position(arena_type_id, base_id)
        bases[team] = {
            'base_id': base_id,
            'pos': (float(x), float(z)),
            'radius': BASE_CAPTURE_RADIUS,
            'points': 0.0,
            'last_sent': 0,
            'capturing_team': 0,
            'invader_progress': {},
        }
    return {'last_update': time.time(), 'bases': bases,
            'lock': threading.RLock()}


def build_base_capture_initial_updates(sess: dict):
    state = ensure_battle_capture_state(sess)
    updates = []
    for base_team in sorted(state.get('bases', {})):
        base = state['bases'][base_team]
        updates.append((int(base_team),
                        int(base.get('base_id') or base_team),
                        int(base.get('last_sent', 0))))
    return updates


def ensure_battle_capture_state(sess: dict):
    state = sess.get('battle_capture_state')
    if state is not None:
        return state
    arena_type_id = normalize_arena_type_id(sess.get('battle_arena_type_id'))
    state = build_battle_capture_state(
        arena_type_id, sess.get('battle_base_assignment') or {1: 1, 2: 2})
    sess['battle_capture_state'] = state
    return state


def distance_xz(pos, base_pos) -> float:
    dx = float(pos[0]) - float(base_pos[0])
    dz = float(pos[2]) - float(base_pos[1])
    return math.sqrt(dx * dx + dz * dz)


def get_base_capture_vehicle_pos(sess: dict):
    pos = sess.get('client_vehicle_pos') if not sess.get('server_vehicle_authoritative', True) else None
    if pos is not None:
        last = float(sess.get('client_vehicle_last_update_time', 0.0))
        if time.time() - last <= BASE_CAPTURE_CLIENT_POSITION_MAX_AGE:
            return pos
    return get_effective_vehicle_pos(
        sess, sess.get('battle_pos', ARENA_SPAWN_POS[ARENA_TYPE_KARELIA]))


def assign_match_teams(valid_batch):
    shuffled = list(valid_batch)
    random.shuffle(shuffled)
    if MATCHMAKING_TEAM_BALANCE_MODE == 'health_weight':
        shuffled.sort(key=lambda queued: int(get_session_battle_vehicle(
            queued.get('sess') or {}).get(MATCHMAKING_TEAM_WEIGHT_FIELD, 0)),
            reverse=True)
    teams = {1: [], 2: []}
    weights = {1: 0, 2: 0}
    for queued in shuffled:
        vehicle = get_session_battle_vehicle(queued.get('sess') or {})
        if MATCHMAKING_TEAM_BALANCE_MODE == 'count':
            weight = 1
        else:
            weight = max(1, int(vehicle.get(MATCHMAKING_TEAM_WEIGHT_FIELD, 1)))
        if len(teams[1]) != len(teams[2]):
            team = 1 if len(teams[1]) < len(teams[2]) else 2
        else:
            team = 1 if weights[1] <= weights[2] else 2
        teams[team].append(queued)
        weights[team] += weight

    base_assignment = {1: 1, 2: 2}
    for team, queued_list in teams.items():
        for index, queued in enumerate(queued_list):
            sess = queued.get('sess')
            if not sess:
                continue
            sess['battle_team'] = team
            sess['battle_spawn_index'] = index
            sess['battle_base_assignment'] = dict(base_assignment)


def pick_legacy_spawn_pos(arena_type_id: int, sess: dict):
    if arena_type_id == ARENA_TYPE_KARELIA:
        account_id = int(sess.get('account_id') or 1)
        return KARELIA_TEAM1_SPAWNS[(account_id - 1) % len(KARELIA_TEAM1_SPAWNS)]
    return ARENA_SPAWN_POS.get(arena_type_id, ARENA_SPAWN_POS[ARENA_TYPE_KARELIA])


def current_server_time(sess: dict) -> int:
    zero_wall_time = sess.get('server_time_zero_wall')
    if zero_wall_time is None:
        return 0
    return max(0, int(time.time() - zero_wall_time))


def wall_time_to_server_time(sess: dict, wall_time: float) -> int:
    zero_wall_time = sess.get('server_time_zero_wall')
    if zero_wall_time is None:
        return max(0, int(math.ceil(
            float(wall_time) - BATTLE_PERIOD_TIME_OFFSET_SECONDS)))
    return max(0, int(math.ceil(
        float(wall_time) - float(zero_wall_time) -
        BATTLE_PERIOD_TIME_OFFSET_SECONDS)))


def get_session_battle_start_wall(sess: dict) -> float:
    value = sess.get('battle_start_wall')
    if value is None:
        value = time.time() + PREBATTLE_TIMER_SECONDS
        sess['battle_start_wall'] = value
    return float(value)


def get_session_battle_end_wall(sess: dict) -> float:
    value = sess.get('battle_end_wall')
    if value is None:
        value = get_session_battle_start_wall(sess) + BATTLE_TIMER_SECONDS
        sess['battle_end_wall'] = value
    return float(value)


def sync_match_battle_timers(sessions, start_wall=None, launch_wall=None):
    sessions = [sess for sess in sessions if sess is not None]
    if not sessions:
        now = time.time()
        return now + PREBATTLE_TIMER_SECONDS, now + PREBATTLE_TIMER_SECONDS + BATTLE_TIMER_SECONDS
    if start_wall is None:
        values = [
            float(sess.get('battle_start_wall'))
            for sess in sessions
            if sess.get('battle_start_wall') is not None
        ]
        start_wall = max(values) if values else time.time() + PREBATTLE_TIMER_SECONDS
    end_wall = float(start_wall) + BATTLE_TIMER_SECONDS
    for sess in sessions:
        sess['battle_start_wall'] = float(start_wall)
        sess['battle_end_wall'] = end_wall
        if launch_wall is not None:
            sess['battle_launch_wall'] = float(launch_wall)
    return float(start_wall), end_wall


def sync_prebattle_countdown_after_ready(sessions):
    sessions = [sess for sess in sessions if sess is not None]
    if not sessions:
        return time.time() + PREBATTLE_TIMER_SECONDS
    if any(sess.get('battle_period_active') for sess in sessions):
        start_wall, _end_wall = sync_match_battle_timers(sessions)
        return start_wall
    values = [
        float(sess.get('battle_start_wall'))
        for sess in sessions
        if sess.get('battle_start_wall') is not None
    ]
    start_wall = max(values) if values else time.time() + PREBATTLE_TIMER_SECONDS
    start_wall, _end_wall = sync_match_battle_timers(sessions, start_wall=start_wall)
    return start_wall


def send_avatar_arena_update(sock, addr, sess, update_type: int, data, label: str):
    msg = build_avatar_update_arena(update_type, data)
    send_lock = sess.setdefault('send_lock', threading.RLock())
    try:
        with send_lock:
            pkt = build_channel_packet(msg, sess, reliable=True)
            pkt = bw_encrypt_packet(pkt, sess['bf_key'])
            runtime_sendto(sock, pkt, addr)
    except Exception:
        return
    print(f"    [>] Avatar.updateArena({label})")


def send_avatar_messages(sock, addr, sess, msgs: bytes, label: str,
                         reliable: bool = True):
    if not addr:
        return False
    send_lock = sess.setdefault('send_lock', threading.RLock())
    try:
        with send_lock:
            pkt = build_channel_packet(msgs, sess, reliable=reliable)
            pkt = bw_encrypt_packet(pkt, sess['bf_key'])
            runtime_sendto(sock, pkt, addr)
    except Exception:
        return False
    if label:
        print(f"    [>] {label}")
    return True


def init_battle_state(sess: dict, spawn_pos):
    vehicle = get_session_battle_vehicle(sess)
    spawn_yaw = spawn_yaw_for_base(get_session_spawn_base(sess))
    fwd, bwd = get_vehicle_speed_limits(vehicle)
    compact = sess.get('battle_vehicle_compactDescr') or (vehicle or {}).get('compactDescr') or b''
    compact_hex = compact.hex() if isinstance(compact, (bytes, bytearray)) else str(compact)
    print(f"    [battle-vehicle] player={sess.get('username') or 'player'} "
          f"invID={sess.get('battle_vehicle_inv_id')} "
          f"name={(vehicle or {}).get('name', 'unknown')} "
          f"class={(vehicle or {}).get('vehicleClass') or 'unknown'} "
          f"compact={compact_hex} fwd={fwd * 3.6:.1f}km/h "
          f"rev={bwd * 3.6:.1f}km/h hp={get_vehicle_max_health(vehicle)}")
    print(f"    [motion] {format_battle_motion_params(vehicle)}")
    sess['battle_generation'] = sess.get('battle_generation', 0) + 1
    arena_type_id = normalize_arena_type_id(sess.get('battle_arena_type_id'))
    sx, sy, sz = (float(v) for v in spawn_pos)
    sy, _normal = sample_terrain(arena_type_id, sx, sz, sy)
    sess['battle_pos'] = (sx, sy, sz)
    sess['battle_prev_pos'] = sess['battle_pos']
    sess['battle_yaw'] = spawn_yaw
    sess['battle_speed'] = 0.0
    sess['battle_rspeed'] = 0.0
    sess['battle_target_speed'] = 0.0
    sess['battle_target_rspeed'] = 0.0
    sess['battle_last_motion_time'] = time.time()
    sess['battle_motion_flags'] = 0
    sess['battle_motion_loop_started'] = False
    sess['battle_client_control_enabled'] = False
    sess['server_vehicle_authoritative'] = True
    sess['client_vehicle_pos'] = None
    sess['client_vehicle_yaw'] = spawn_yaw
    sess['client_vehicle_last_update_time'] = 0.0
    sess['client_vehicle_last_move_time'] = 0.0
    sess['client_vehicle_slope'] = 0.0
    sess['client_vehicle_slope_time'] = 0.0
    sess['client_vehicle_slope_sample_count'] = 0
    sess['battle_period_active'] = False
    sess['battle_turret_yaw'] = spawn_yaw
    sess['battle_gun_pitch'] = 0.0
    sess.pop('battle_start_wall', None)
    sess.pop('battle_end_wall', None)
    sess['battle_targeting_state_time'] = time.time()
    sess['battle_current_shell'] = 0
    sess['battle_next_shell'] = 0
    sess['battle_ammo_stock'] = build_vehicle_ammo_stock(vehicle)
    sess['battle_vehicle_health'] = get_vehicle_max_health(vehicle)
    sess['battle_reload_until'] = 0.0
    sess['battle_shells_fired'] = {}
    sess['battle_last_shot_time'] = 0.0
    sess['battle_last_spotting_shot_time'] = 0.0
    sess['battle_visual_shot_viewers'] = {}
    sess['battle_shot_id'] = 1
    sess['battle_shots'] = 0
    sess['battle_hits'] = 0
    sess['battle_shots_received'] = 0
    sess['battle_damage_dealt'] = 0
    sess['battle_damage_received'] = 0
    sess['battle_capture_points'] = 0
    sess['battle_dropped_capture_points'] = 0
    sess['battle_damaged_vehicle_ids'] = set()
    sess['battle_killed_vehicle_ids'] = set()
    sess['battle_spotted_vehicle_ids'] = set()
    sess['battle_frags'] = 0
    sess['battle_killer_account_id'] = 0
    sess['battle_ended'] = False
    sess['avatar_ready_sent'] = False
    sess['battle_client_ready'] = False
    sess['battle_period_timer_started'] = False
    sess['battle_late_period_timer_started'] = False
    sess['battle_forced_position_sent_for_motion'] = False
    sess['battle_last_filter_reset_time'] = None
    sess['battle_last_own_vehicle_sync_time'] = 0.0


def approach(current: float, target: float, rate: float, dt: float) -> float:
    if current < target:
        return min(target, current + rate * dt)
    if current > target:
        return max(target, current - rate * dt)
    return current


def compute_hull_aim_autorotation_target_rspeed(sess: dict, vehicle: dict,
                                                yaw: float) -> float:
    """For SPG/AT-SPG that are standing still with no rotation keys held,
    return the rotation rate needed to align the hull with the aim point
    (`battle_target_pos`).  Returns 0.0 when no rotation is needed (already
    aligned within the dead-band, no aim point, or aim point too close).

    The hull rotates at the chassis rotation limit toward the desired yaw,
    matching the response one would get by holding the manual rotation key."""
    target_pos = sess.get('battle_target_pos')
    if target_pos is None:
        return 0.0
    pos = sess.get('battle_pos')
    if pos is None:
        return 0.0
    try:
        dx = float(target_pos[0]) - float(pos[0])
        dz = float(target_pos[2]) - float(pos[2])
    except (TypeError, IndexError, ValueError):
        return 0.0
    horizontal = math.sqrt(dx * dx + dz * dz)
    if horizontal < HULL_AIM_AUTOROTATION_MIN_DISTANCE:
        return 0.0
    desired_yaw = math.atan2(dx, dz)
    delta = normalize_angle(desired_yaw - float(yaw))
    if abs(delta) < HULL_AIM_AUTOROTATION_DEAD_ANGLE:
        return 0.0
    rotation_limit = get_vehicle_rotation_speed(vehicle)
    if rotation_limit <= 0.0:
        return 0.0
    return math.copysign(rotation_limit, delta)


def battle_motion_targets(flags: int, vehicle: dict = None):
    forward = bool(flags & 1)
    backward = bool(flags & 2)
    left = bool(flags & 4)
    right = bool(flags & 8)
    movement_dir = 1 if forward and not backward else -1 if backward and not forward else 0
    rotation_dir = -1 if left and not right else 1 if right and not left else 0
    if movement_dir < 0:
        rotation_dir = -rotation_dir

    forward_limit, backward_limit = get_vehicle_speed_limits(vehicle)
    if movement_dir > 0:
        target_speed = forward_limit
    elif movement_dir < 0:
        target_speed = -backward_limit
    else:
        target_speed = 0.0

    rotation_limit = get_vehicle_rotation_speed(vehicle)
    target_rspeed = rotation_limit * rotation_dir
    return target_speed, target_rspeed


def advance_battle_motion(sess: dict, flags: int = None):
    now = time.time()
    last = sess.get('battle_last_motion_time', now)
    dt = max(0.001, min(0.25, now - last))
    sess['battle_last_motion_time'] = now

    x, y, z = sess.get('battle_pos', ARENA_SPAWN_POS[ARENA_TYPE_KARELIA])
    arena_type_id = normalize_arena_type_id(sess.get('battle_arena_type_id'))
    current_height, current_normal = sample_terrain(arena_type_id, x, z, y)
    y = current_height
    prev_pos = (x, y, z)
    yaw = float(sess.get('battle_yaw', 0.0))
    if freeze_destroyed_vehicle(sess):
        sess['battle_prev_pos'] = prev_pos
        sess['battle_pos'] = (x, y, z)
        sess['battle_yaw'] = yaw
        return (x, y, z), yaw, 0.0, 0.0
    if flags is None:
        flags = sess.get('battle_motion_flags', 0)

    vehicle = get_session_battle_vehicle(sess)
    target_speed, target_rspeed = battle_motion_targets(flags, vehicle)
    speed = float(sess.get('battle_speed', 0.0))
    if (HULL_AIM_AUTOROTATION_ENABLED and
            abs(target_rspeed) < 0.001 and
            abs(target_speed) < 0.001 and
            abs(speed) < BATTLE_STOP_SPEED and
            is_hull_aim_autorotation_vehicle(vehicle)):
        target_rspeed = compute_hull_aim_autorotation_target_rspeed(
            sess, vehicle, yaw)
    raw_target_speed = target_speed
    rspeed = float(sess.get('battle_rspeed', 0.0))
    accel, decel = get_vehicle_motion_rates(vehicle)
    rot_accel, rot_decel = get_vehicle_rotation_rates(vehicle)

    min_normal_y = float((vehicle or {}).get('chassisMinPlaneNormalY') or 0.0)
    if min_normal_y <= 0.0:
        climb_angle = float((vehicle or {}).get('chassisMaxClimbAngleRad') or math.radians(35.0))
        min_normal_y = math.cos(climb_angle)

    slope_branch = 'OK'
    candidate_probe_y = y
    candidate_probe_normal = current_normal
    if abs(target_speed) > 0.001:
        probe_dist = max(1.0, min(4.0, abs(speed) * dt + 2.0))
        probe_sign = 1.0 if target_speed > 0.0 else -1.0
        probe_x = x + math.sin(yaw) * probe_dist * probe_sign
        probe_z = z + math.cos(yaw) * probe_dist * probe_sign
        candidate_probe_y, candidate_probe_normal = sample_terrain(arena_type_id, probe_x, probe_z, y)
        if candidate_probe_normal[1] < min_normal_y and candidate_probe_y > y + 0.05:
            target_speed = min(0.0, speed) if target_speed > 0.0 else max(0.0, speed)
            slope_branch = 'CLIMB_LIMIT'
        elif current_normal[1] < min_normal_y and candidate_probe_y > y + 0.05:
            target_speed = 0.0
            slope_branch = 'PLANE_LIMIT'

    same_direction = (
        abs(speed) < 0.001 or abs(target_speed) < 0.001 or
        (speed > 0.0 and target_speed > 0.0) or
        (speed < 0.0 and target_speed < 0.0)
    )
    coasting = not (flags & 1) and not (flags & 2) and abs(target_speed) < 0.001
    if coasting:
        speed_rate = max(BATTLE_COAST_DECEL_MIN,
                         min(BATTLE_COAST_DECEL_MAX,
                             decel * BATTLE_COAST_DECEL_RATIO))
    else:
        speed_rate = accel if same_direction and abs(target_speed) > abs(speed) else decel
    speed = approach(speed, target_speed, speed_rate, dt)

    turn_factor = 1.0
    if abs(target_speed) > 0.01 and abs(speed) > 0.01:
        min_turn_factor = get_vehicle_min_turn_factor(vehicle)
        turn_factor = min_turn_factor
        turn_factor += (1.0 - min_turn_factor) * min(
            1.0, abs(speed) / max(0.01, abs(target_speed)))
    target_rspeed *= turn_factor
    rspeed_rate = rot_accel if abs(target_rspeed) > abs(rspeed) else rot_decel
    rspeed = approach(rspeed, target_rspeed, rspeed_rate, dt)

    if abs(target_speed) < 0.001 and abs(speed) < BATTLE_STOP_SPEED:
        speed = 0.0
    if abs(target_rspeed) < 0.001 and abs(rspeed) < BATTLE_STOP_ROT_SPEED:
        rspeed = 0.0
    prev_yaw = yaw
    yaw += rspeed * dt
    yaw = normalize_angle(yaw)

    blocked_dbg = False
    if abs(speed) > 0.001:
        cand_x = x + math.sin(yaw) * speed * dt
        cand_z = z + math.cos(yaw) * speed * dt
        new_x, new_z, blocked = resolve_motion_against_obstacles(
            arena_type_id, x, z, cand_x, cand_z)
        if not blocked:
            new_x, new_z, blocked = resolve_motion_against_vehicles(
                sess, x, z, new_x, new_z, yaw=yaw,
                source_speed=speed, source_rspeed=rspeed)
        new_y, new_normal = sample_terrain(arena_type_id, new_x, new_z, y)
        uphill_blocked = new_normal[1] < min_normal_y and new_y > y + 0.05
        if uphill_blocked:
            blocked = True
        if not blocked:
            x, y, z = new_x, new_y, new_z
        if blocked:
            speed = 0.0
            blocked_dbg = True
    else:
        y = current_height

    prev_branch = sess.get('battle_dbg_last_slope_branch', 'none')
    last_log = float(sess.get('battle_dbg_last_log_time', 0.0))
    branch_changed = (prev_branch != slope_branch)
    blocked_changed = (bool(sess.get('battle_dbg_last_blocked', False)) != blocked_dbg)
    should_log = (
        BATTLE_VERBOSE_DEBUG and
        (flags != 0 or abs(speed) > 0.05) and
        (now - last_log >= 0.5 or branch_changed or blocked_changed)
    )
    if should_log:
        client_pos = sess.get('client_vehicle_pos')
        if client_pos:
            cy = client_pos[1]
            srv_y = y
            y_diff = cy - srv_y
            client_str = (f"client=({client_pos[0]:.1f},{cy:.1f},{client_pos[2]:.1f}) "
                          f"dY={y_diff:+.2f}")
        else:
            client_str = "client=None"
        veh_name = (vehicle or {}).get('name', '?')
        veh_fwd, veh_bwd = get_vehicle_speed_limits(vehicle)
        uname = sess.get('username') or 'player'
        print(f"    [motion] {uname}/{veh_name} fwdLim={veh_fwd*3.6:.1f}km/h "
              f"accel={accel:.2f} flags=0x{flags:02x} "
              f"pos=({x:.1f},{y:.1f},{z:.1f}) "
              f"yaw={yaw:.2f} spd={speed:+.2f}m/s({speed * 3.6:+.1f}km/h)"
              f"->t={target_speed:+.2f}m/s({target_speed * 3.6:+.1f}km/h)"
              f"(raw={raw_target_speed:+.2f}m/s/{raw_target_speed * 3.6:+.1f}km/h) "
              f"normalY={current_normal[1]:+.3f}->{candidate_probe_normal[1]:+.3f} "
              f"terrainY={current_height:.2f}->{candidate_probe_y:.2f} "
              f"branch={slope_branch} blocked={blocked_dbg} dt={dt*1000:.0f}ms "
              f"{client_str}")
        sess['battle_dbg_last_log_time'] = now
        sess['battle_dbg_last_slope_branch'] = slope_branch
        sess['battle_dbg_last_blocked'] = blocked_dbg

    sess['battle_prev_pos'] = prev_pos
    sess['battle_pos'] = (x, y, z)
    sess['battle_yaw'] = yaw
    if is_hull_locked_gun_vehicle(vehicle):
        sess['battle_turret_yaw'] = yaw
    sess['battle_speed'] = speed
    sess['battle_rspeed'] = rspeed
    sess['battle_target_speed'] = target_speed
    sess['battle_target_rspeed'] = target_rspeed
    return (x, y, z), yaw, speed, rspeed


def send_own_vehicle_position(sock, addr, sess, label: str = '',
                              reliable: bool = False, force: bool = False):
    pos = sess.get('battle_pos', ARENA_SPAWN_POS[ARENA_TYPE_KARELIA])
    yaw = sess.get('battle_yaw', 0.0)
    speed = sess.get('battle_speed', 0.0)
    rspeed = sess.get('battle_rspeed', 0.0)
    return send_avatar_messages(
        sock, addr, sess,
        build_own_vehicle_motion_update(pos, yaw, speed, rspeed, force=force),
        label,
        reliable=reliable)


def enable_client_vehicle_control(sock, addr, sess, label: str = ''):
    if sess.get('battle_client_control_enabled'):
        return
    sess['battle_client_control_enabled'] = True
    msgs = build_control_entity(PLAYER_VEHICLE_ID, True)
    send_avatar_messages(sock, addr, sess, msgs,
                         label or "controlEntity(Vehicle, on)",
                         reliable=True)


def disable_client_vehicle_control_message(sess):
    sess['battle_client_control_enabled'] = False
    return build_control_entity(PLAYER_VEHICLE_ID, False)


def send_destroyed_vehicle_freeze(sock, sess: dict) -> bool:
    if not freeze_destroyed_vehicle(sess):
        return False
    addr = sess.get('addr')
    if not addr:
        return False
    pos = sess.get('battle_pos', ARENA_SPAWN_POS[ARENA_TYPE_KARELIA])
    yaw = float(sess.get('battle_yaw', 0.0))
    msgs = b''
    if sess.get('battle_client_control_enabled'):
        msgs += disable_client_vehicle_control_message(sess)
    msgs += build_forced_position(PLAYER_VEHICLE_ID, pos, yaw,
                                  space_id=SPACE_ID, vehicle_id=0)
    return send_avatar_messages(sock, addr, sess, msgs,
                                "Vehicle.destroyed freeze",
                                reliable=True)


def handle_client_avatar_update(sess: dict, msg_id: int, payload: bytes):
    dbg_count = sess.get('avu_dbg_count', 0) + 1
    sess['avu_dbg_count'] = dbg_count
    log_this = BATTLE_VERBOSE_DEBUG and (dbg_count % 100 == 1)

    if msg_id == 2:
        decoded = decode_client_coord_ypr(payload, 0)
        if decoded is None:
            return False
        pos, yaw, _pitch, _roll = decoded
        sess['avatar_pos'] = pos
        sess['avatar_yaw'] = yaw
        plausible = is_plausible_avatar_vehicle_position(sess, pos)
        if log_this:
            base = sess.get('client_vehicle_pos') or sess.get('battle_pos')
            base_str = f"({base[0]:.1f},{base[1]:.1f},{base[2]:.1f})" if base else "None"
            print(f"    [avu] msg=0x02 avatarUpdateImplicit pos=({pos[0]:.1f},{pos[1]:.1f},{pos[2]:.1f}) "
                  f"yaw={yaw:.2f} base={base_str} plausible={plausible} bundle={sess.get('battle_bundle_sent', False)}")
        return True

    if msg_id == 3:
        if len(payload) < 25:
            return False
        rel_id = struct.unpack_from('<I', payload, 0)[0]
        pos, yaw, _pitch, _roll = decode_client_coord_ypr(payload, 8)
        sess['avatar_pos'] = pos
        sess['avatar_yaw'] = yaw
        plausible = is_plausible_avatar_vehicle_position(sess, pos)
        if log_this:
            print(f"    [avu] msg=0x03 avatarUpdate rel_id={rel_id} pos=({pos[0]:.1f},{pos[1]:.1f},{pos[2]:.1f}) "
                  f"yaw={yaw:.2f} plausible={plausible}")
        return True

    if msg_id == 4:
        if len(payload) < 19:
            return False
        ward_id = struct.unpack_from('<I', payload, 0)[0]
        decoded = decode_client_coord_ypr(payload, 4)
        if decoded is None:
            return False
        pos, yaw, _pitch, _roll = decoded
        if log_this:
            print(f"    [avu] msg=0x04 wardUpdate ward_id={ward_id} pos=({pos[0]:.1f},{pos[1]:.1f},{pos[2]:.1f}) "
                  f"yaw={yaw:.2f} match_PV={ward_id == PLAYER_VEHICLE_ID}")
        if ward_id == PLAYER_VEHICLE_ID:
            record_client_vehicle_position(sess, pos, yaw)
            count = sess.get('client_vehicle_update_count', 0) + 1
            sess['client_vehicle_update_count'] = count
            if BATTLE_VERBOSE_DEBUG and count % 100 == 1:
                print(f"[<] client Vehicle#{ward_id} pos="
                      f"({pos[0]:.1f},{pos[1]:.1f},{pos[2]:.1f}) "
                      f"yaw={yaw:.2f}")
        return True

    if msg_id == 5:
        if len(payload) < 28:
            return False
        ward_id = struct.unpack_from('<I', payload, 0)[0]
        rel_id = struct.unpack_from('<I', payload, 4)[0]
        decoded = decode_client_coord_ypr(payload, 12)
        if decoded is None:
            return False
        pos, yaw, _pitch, _roll = decoded
        if log_this:
            print(f"    [avu] msg=0x05 wardUpdate ward_id={ward_id} rel_id={rel_id} "
                  f"pos=({pos[0]:.1f},{pos[1]:.1f},{pos[2]:.1f}) yaw={yaw:.2f} match_PV={ward_id == PLAYER_VEHICLE_ID}")
        if ward_id == PLAYER_VEHICLE_ID:
            record_client_vehicle_position(sess, pos, yaw)
        return True

    return False


def normalize_vec(vec):
    vec = safe_vec3(vec, (0.0, 0.0, 1.0))
    x, y, z = vec
    length = math.sqrt(x * x + y * y + z * z)
    if length <= 0.0001:
        return (0.0, 0.0, 1.0)
    return (x / length, y / length, z / length)


def safe_float(value, default=0.0, min_value=None, max_value=None):
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        value = float(default)
    if not math.isfinite(value):
        value = float(default)
    if min_value is not None:
        value = max(float(min_value), value)
    if max_value is not None:
        value = min(float(max_value), value)
    return value


def safe_dict(value):
    return dict(value) if isinstance(value, dict) else {}


def safe_vec3(value, default=(0.0, 0.0, 0.0)):
    if value is None:
        return default
    try:
        if len(value) < 3:
            return default
        out = (float(value[0]), float(value[1]), float(value[2]))
    except (TypeError, ValueError, OverflowError):
        return default
    if not all(math.isfinite(v) for v in out):
        return default
    return out


def safe_effects_index(value):
    try:
        value = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    if value < 0 or value > 255:
        return 0
    return value


def normalize_artillery_target_pos(sess: dict, target_pos):
    target_pos = safe_vec3(target_pos, None)
    if target_pos is None:
        return None
    arena_type_id = normalize_arena_type_id(
        sess.get('battle_arena_type_id') or ARENA_TYPE_KARELIA)
    terrain_y, _normal = sample_terrain(
        arena_type_id, target_pos[0], target_pos[2], target_pos[1])
    return (target_pos[0], terrain_y, target_pos[2])


def build_targeting_for_point(sess: dict, target_pos):
    pos = get_effective_vehicle_pos(
        sess, sess.get('battle_pos', ARENA_SPAWN_POS[ARENA_TYPE_KARELIA]))
    shot_pos = (pos[0], pos[1] + 2.0, pos[2])
    now = time.time()
    shell = get_current_vehicle_shell(sess)
    vehicle = get_session_battle_vehicle(sess)
    high_arc = is_artillery_vehicle(vehicle)
    if high_arc:
        target_pos = normalize_artillery_target_pos(sess, target_pos)
    else:
        target_pos = safe_vec3(target_pos, None)
    if target_pos is None:
        target_pos = (
            shot_pos[0],
            shot_pos[1],
            shot_pos[2] + 1.0,
        )
    dx = target_pos[0] - shot_pos[0]
    dy = target_pos[1] - shot_pos[1]
    dz = target_pos[2] - shot_pos[2]
    shot_vec = ballistic_shot_vec(
        shot_pos, target_pos,
        float(shell.get('speed', 800.0)),
        float(shell.get('gravity', 9.81)),
        high_arc=high_arc)
    desired_turret_yaw = normalize_angle(math.atan2(dx, dz))
    desired_gun_pitch = math.atan2(
        shot_vec[1], max(0.001, math.sqrt(shot_vec[0] * shot_vec[0] + shot_vec[2] * shot_vec[2])))
    last = float(sess.get('battle_targeting_state_time', now))
    dt = max(0.001, min(0.2, now - last))
    sess['battle_targeting_state_time'] = now
    turret_speed = float(vehicle.get('turretRotationSpeed', 0.5235987755982988))
    gun_speed = float(vehicle.get('gunRotationSpeed', 0.5235987755982988))
    hull_yaw = float(sess.get('battle_yaw', 0.0))
    hull_locked = is_hull_locked_gun_vehicle(vehicle)
    if hull_locked:
        desired_turret_yaw = hull_yaw
    current_turret_yaw = float(sess.get('battle_turret_yaw', hull_yaw))
    if hull_locked:
        current_turret_yaw = hull_yaw
    current_gun_pitch = float(sess.get('battle_gun_pitch', 0.0))
    turret_delta = normalize_angle(desired_turret_yaw - current_turret_yaw)
    gun_delta = desired_gun_pitch - current_gun_pitch
    turret_step = clamp(turret_delta, -turret_speed * dt, turret_speed * dt)
    gun_step = clamp(gun_delta, -gun_speed * dt, gun_speed * dt)
    turret_yaw = hull_yaw if hull_locked else normalize_angle(current_turret_yaw + turret_step)
    gun_pitch = current_gun_pitch + gun_step
    sess['battle_turret_yaw'] = turret_yaw
    sess['battle_gun_pitch'] = gun_pitch
    sess['battle_target_pos'] = tuple(float(v) for v in target_pos)
    sess['battle_target_pos_time'] = now
    sess['battle_shot_pos'] = shot_pos
    current_shot_vec = shot_vec_from_angles(turret_yaw, gun_pitch)
    marker_shot_vec = current_shot_vec
    sess['battle_shot_vec'] = marker_shot_vec
    dispersion_angle = (shot_dispersion_angle(shot_pos, target_pos)
                        if SHOT_DISPERSION_ENABLED and not high_arc
                        else 0.03)
    return (
        build_avatar_update_targeting_info(
            normalize_angle(turret_yaw - float(sess.get('battle_yaw', 0.0))),
            gun_pitch,
            turret_speed, gun_speed) +
        build_avatar_update_gun_marker(
            shot_pos, marker_shot_vec, dispersion_angle)
    )


def build_battle_vehicle_state_messages(sess: dict):
    vehicle = get_session_battle_vehicle(sess)
    stock = sess.get('battle_ammo_stock') or build_vehicle_ammo_stock(vehicle)
    sess['battle_ammo_stock'] = stock
    health = int(sess['battle_vehicle_health']) if 'battle_vehicle_health' in sess else get_vehicle_max_health(vehicle)
    msgs = build_avatar_update_vehicle_health(health, True)
    for compact, quantity in stock.items():
        msgs += build_avatar_update_vehicle_ammo(compact, quantity, 0)
    current_shell = select_available_shell(stock, sess.get('battle_current_shell', 0))
    if current_shell:
        sess['battle_current_shell'] = current_shell
        sess['battle_next_shell'] = current_shell
        msgs += build_avatar_update_vehicle_setting(0, current_shell)
        msgs += build_avatar_update_vehicle_setting(1, current_shell)
    msgs += build_avatar_update_vehicle_reload(0.0)
    return msgs


def build_battle_period_start_messages(sess: dict):
    end_time = wall_time_to_server_time(sess, get_session_battle_end_wall(sess))
    msgs = build_avatar_update_arena(
        ARENA_UPDATE_PERIOD,
        (ARENA_PERIOD_BATTLE, end_time, BATTLE_TIMER_SECONDS, None))
    for update in build_base_capture_initial_updates(sess):
        msgs += build_avatar_update_arena(ARENA_UPDATE_BASE_POINTS, update)
    msgs += build_battle_motion_sync(
        sess.get('battle_pos', ARENA_SPAWN_POS[ARENA_TYPE_KARELIA]),
        sess.get('battle_yaw', 0.0),
        0.0, 0.0,
        bind_avatar=True)
    sess['battle_last_own_vehicle_sync_time'] = time.time()
    if CLIENT_AUTHORITATIVE_VEHICLE_CONTROL:
        msgs += build_control_entity(PLAYER_VEHICLE_ID, True)
    else:
        msgs += disable_client_vehicle_control_message(sess)
    return msgs


def get_match_battle_sessions(match_id):
    with battle_lock:
        return [
            sess for sess in active_battle_accounts.values()
            if sess.get('battle_match_id') == match_id and
            sess.get('battle_bundle_sent')
        ]


def broadcast_match_avatar_ready(sock, ready_sess: dict):
    match_id = ready_sess.get('battle_match_id')
    if match_id is None:
        sessions = [ready_sess]
    else:
        sessions = get_match_battle_sessions(match_id)
    for viewer in sessions:
        addr = viewer.get('addr')
        if not addr:
            continue
        vehicle_id = viewer_vehicle_id(viewer, ready_sess)
        send_avatar_messages(
            sock, addr, viewer,
            build_avatar_update_arena(ARENA_UPDATE_AVATAR_READY, vehicle_id),
            "Avatar.updateArena(AVATAR_READY)",
            reliable=True)


def schedule_battle_period(sock, addr, sess):
    match_id = sess.get('battle_match_id')
    sessions = (
        get_match_battle_sessions(match_id)
        if match_id is not None else [sess])
    if not sessions:
        return
    start_wall, _end_wall = sync_match_battle_timers(sessions)
    if any(player.get('battle_period_timer_started') for player in sessions):
        if sess.get('battle_client_ready') and not sess.get('battle_period_active'):
            if start_wall <= time.time() and not sess.get('battle_late_period_timer_started'):
                generation = sess.get('battle_generation', 0)
                sess['battle_late_period_timer_started'] = True

                def _start_late_battle():
                    if generation != sess.get('battle_generation', 0):
                        return
                    if not sess.get('battle_client_ready'):
                        return
                    if sess.get('battle_ended') or not sess.get('battle_bundle_sent'):
                        return
                    if start_battle_period_for_session(sock, sess):
                        start_battle_tick_loop(sock)

                runtime_call_later(BATTLE_READY_GUARD_SECONDS, _start_late_battle)
        return
    generations = {
        id(player): player.get('battle_generation', 0)
        for player in sessions
    }
    for player in sessions:
        player['battle_period_timer_started'] = True

    def _start_battle():
        current_sessions = (
            get_match_battle_sessions(match_id)
            if match_id is not None else [sess])
        if not current_sessions:
            return
        current_start_wall, _end_wall = sync_match_battle_timers(current_sessions)
        remaining = current_start_wall - time.time()
        if remaining > 0.05:
            runtime_call_later(remaining, _start_battle)
            return
        started = False
        for player in current_sessions:
            if generations.get(id(player)) != player.get('battle_generation', 0):
                continue
            if player.get('battle_ended') or not player.get('battle_bundle_sent'):
                continue
            if start_battle_period_for_session(sock, player):
                started = True
        if started:
            start_battle_tick_loop(sock)

    delay = start_wall - time.time()
    if delay <= 0.0:
        delay = BATTLE_READY_GUARD_SECONDS
    runtime_call_later(delay, _start_battle)


def start_battle_period_for_session(sock, sess: dict) -> bool:
    if sess.get('battle_period_active'):
        return False
    player_addr = sess.get('addr')
    if not player_addr:
        return False
    now = time.time()
    msgs = build_battle_period_start_messages(sess)
    sent = send_avatar_messages(
        sock, player_addr, sess, msgs,
        "PERIOD=BATTLE + client vehicle control"
        if CLIENT_AUTHORITATIVE_VEHICLE_CONTROL
        else "PERIOD=BATTLE + server vehicle control")
    if not sent:
        return False
    sess['battle_period_active'] = True
    sess['server_vehicle_authoritative'] = not CLIENT_AUTHORITATIVE_VEHICLE_CONTROL
    sess['battle_client_control_enabled'] = CLIENT_AUTHORITATIVE_VEHICLE_CONTROL
    sess['battle_last_motion_time'] = now
    return True


def send_avatar_player(sock, addr, sess):
    """РЁР»Рµ РїРѕРІРЅРёР№ battle-bundle:
       createBasePlayer(Avatar) + createCellPlayer(Avatar, playerVehicleID=200)
       + createEntity(Vehicle 200) + enterAoI(Vehicle 200)."""
    battle_vehicle = get_session_battle_vehicle(sess)
    veh_compact = (sess.get('battle_vehicle_compactDescr') or
                   (battle_vehicle or {}).get('compactDescr'))
    if not battle_vehicle or not veh_compact:
        print(f"    [!] cannot create battle vehicle for "
              f"{sess.get('username') or 'player'} "
              f"invID={sess.get('battle_vehicle_inv_id')}")
        return
    arena_type_id = normalize_arena_type_id(sess.get('battle_arena_type_id'))
    sess['battle_arena_type_id'] = arena_type_id
    spawn_pos = pick_spawn_pos(arena_type_id, sess)
    init_battle_state(sess, spawn_pos)
    spawn_yaw = sess.get('battle_yaw', 0.0)
    prebattle_left = 0
    roster_sessions = get_viewer_roster_sessions(sess)
    vehicle_list = build_match_vehicle_roster_for_viewer(sess, roster_sessions)
    statistics_list = build_match_vehicle_statistics_for_viewer(sess, roster_sessions)
    msgs = build_avatar_player_bundle(arena_type_id=arena_type_id,
                                      vehicle_compact_descr=veh_compact,
                                      vehicle_data=battle_vehicle,
                                      player_name=sess.get('username') or 'player',
                                      team=int(sess.get('battle_team') or 1),
                                      spawn_pos=spawn_pos,
                                      spawn_yaw=spawn_yaw,
                                      initial_period=ARENA_PERIOD_WAITING,
                                      period_end_time=0,
                                      period_length=0,
                                      vehicle_list=vehicle_list,
                                      statistics_list=statistics_list,
                                      battle_id=int(sess.get('battle_match_id') or 1))
    pkt = build_channel_packet(msgs, sess, reliable=True)
    pkt = bw_encrypt_packet(pkt, sess['bf_key'])
    try:
        runtime_sendto(sock, pkt, addr)
    except Exception:
        return
    sess['battle_bundle_sent'] = True
    sess['known_remote_accounts'] = set()
    remember_match_vehicle_roster(sess, roster_sessions)
    announce_battle_player(sock, sess)
    print(f"    [>] battle-bundle: createBasePlayer(Avatar #{AVATAR_ENTITY_ID}) "
          f"+ spaceData(arenaType={arena_type_id}) "
          f"+ createCellPlayer(playerVeh=#{PLAYER_VEHICLE_ID}) + "
        f"enterAoI + createEntity(Vehicle, invID={sess.get('battle_vehicle_inv_id', 1)}, "
        f"team={sess.get('battle_team', 1)}, base={get_session_spawn_base(sess)}, "
        f"spawn={spawn_pos}, health={get_vehicle_max_health(battle_vehicle)}, "
        f"prebattle={prebattle_left}s)")


def choose_match_arena_type_id(valid_batch):
    first_sess = (valid_batch[0].get('sess') or {}) if valid_batch else {}
    requested = first_sess.get('battle_arena_type_id')
    if requested is not None:
        return normalize_arena_type_id(requested)
    arena_ids = sorted(ENABLED_ARENA_TYPE_IDS)
    if not arena_ids:
        return ARENA_TYPE_FALLBACK
    return normalize_arena_type_id(random.choice(arena_ids))


def start_matchmaking_timer(sock):
    global matchmaking_timer, next_battle_id
    delay = random.uniform(MATCHMAKING_MIN_SECONDS, MATCHMAKING_MAX_SECONDS)

    def _launch():
        global matchmaking_timer, next_battle_id
        with queue_lock:
            batch = list(matchmaking_queue)
            matchmaking_queue[:] = []
            matchmaking_timer = None
        if not batch:
            return
        with battle_lock:
            battle_id = next_battle_id
            next_battle_id += 1
        valid_batch = []
        launch_wall = time.time()
        for queued in batch:
            sess = queued.get('sess')
            addr = queued.get('addr')
            if not sess or not addr or not sess.get('queued_for_battle'):
                continue
            sess['queued_for_battle'] = False
            sess['battle_match_id'] = battle_id
            sess['battle_launch_wall'] = launch_wall
            valid_batch.append(queued)
        if not valid_batch:
            return
        assign_match_teams(valid_batch)
        roster_sessions = [
            queued.get('sess') for queued in valid_batch
            if queued.get('sess') is not None
        ]
        for sess in roster_sessions:
            sess['battle_roster_sessions'] = roster_sessions
        arena_type_id = choose_match_arena_type_id(valid_batch)
        capture_state = build_battle_capture_state(
            arena_type_id,
            (valid_batch[0].get('sess') or {}).get('battle_base_assignment') or {1: 1, 2: 2})
        for queued in valid_batch:
            sess = queued.get('sess')
            if sess:
                sess['battle_arena_type_id'] = arena_type_id
                sess['battle_capture_state'] = capture_state
        if arena_type_id in STATIC_OBSTACLE_CACHE:
            print(f"    [battle] static obstacles cache warm "
                  f"arenaType={arena_type_id} "
                  f"count={len(STATIC_OBSTACLE_CACHE[arena_type_id])}")
        else:
            print(f"    [!] static obstacles cache cold "
                  f"arenaType={arena_type_id}; startup prewarm did not run")
        print(f"    [battle] capture bases={capture_state.get('bases')}")
        team_counts = {1: 0, 2: 0}
        base_assignment = {}
        for queued in valid_batch:
            sess = queued.get('sess') or {}
            team = int(sess.get('battle_team') or 1)
            team_counts[team] = team_counts.get(team, 0) + 1
            base_assignment = sess.get('battle_base_assignment') or base_assignment
        print(f"    [matchmaker] launching battle #{battle_id} for "
              f"{len(valid_batch)} player(s), prebattle after ready "
              f"{PREBATTLE_TIMER_SECONDS}s, teams="
              f"{team_counts.get(1, 0)}:{team_counts.get(2, 0)}, "
              f"bases={base_assignment}")
        for queued in valid_batch:
            sess = queued.get('sess')
            addr = queued.get('addr')
            try:
                send_account_event(sock, addr, sess,
                                   ACCOUNT_ONARENACREATED_MSG_ID,
                                   "Account.onArenaCreated()  [matchmaker]")
            except Exception as e:
                print(f"    [!] matchmaker arena event error: {e}")
        for queued in valid_batch:
            sess = queued.get('sess')
            addr = queued.get('addr')
            try:
                send_avatar_player(sock, addr, sess)
            except Exception as e:
                print(f"    [!] matchmaker switch_to_avatar error: {e}")

    matchmaking_timer = runtime_call_later(delay, _launch)
    print(f"    [matchmaker] battle starts in {delay:.1f}s")

def enqueue_for_matchmaking(sock, addr, sess):
    global matchmaking_timer
    with queue_lock:
        for queued in matchmaking_queue:
            if queued.get('sess') is sess:
                return
        sess['queued_for_battle'] = True
        matchmaking_queue.append({'addr': addr, 'sess': sess})
        queue_len = len(matchmaking_queue)
        timer_needed = matchmaking_timer is None
    print(f"    [matchmaker] queued {sess.get('username') or 'player'} "
          f"({queue_len} in queue)")
    if timer_needed:
        start_matchmaking_timer(sock)

def dequeue_from_matchmaking(sess):
    global matchmaking_timer
    timer_to_cancel = None
    with queue_lock:
        matchmaking_queue[:] = [
            queued for queued in matchmaking_queue
            if queued.get('sess') is not sess
        ]
        if not matchmaking_queue and matchmaking_timer is not None:
            timer_to_cancel = matchmaking_timer
            matchmaking_timer = None
    sess['queued_for_battle'] = False
    if timer_to_cancel is not None and hasattr(timer_to_cancel, 'cancel'):
        timer_to_cancel.cancel()


def send_avatar_ready_and_prebattle(sock, addr, sess):
    if sess.get('avatar_ready_sent'):
        return
    sess['avatar_ready_sent'] = True
    sess['battle_client_ready'] = True
    pos = sess.get('battle_pos', ARENA_SPAWN_POS[ARENA_TYPE_KARELIA])
    yaw = sess.get('battle_yaw', 0.0)
    initial_target = (pos[0] + math.sin(yaw) * 100.0,
                      pos[1] + 2.0,
                      pos[2] + math.cos(yaw) * 100.0)
    msgs = b''
    msgs += build_battle_motion_sync(pos, yaw, 0.0, 0.0,
                                     bind_avatar=True)
    msgs += build_battle_vehicle_state_messages(sess)
    msgs += build_targeting_for_point(sess, initial_target)
    send_avatar_messages(sock, addr, sess, msgs,
                         "Avatar ready + initial vehicle position/targeting")
    match_id = sess.get('battle_match_id')
    sessions = (
        get_match_battle_sessions(match_id)
        if match_id is not None else [sess])
    broadcast_match_avatar_ready(sock, sess)
    if not all(player.get('battle_client_ready') for player in sessions):
        return
    start_wall = sync_prebattle_countdown_after_ready(sessions)
    for viewer in sessions:
        if not viewer.get('addr'):
            continue
        send_avatar_arena_update(
            sock, viewer.get('addr'), viewer, ARENA_UPDATE_PERIOD,
            (ARENA_PERIOD_PREBATTLE,
             wall_time_to_server_time(viewer, start_wall),
             PREBATTLE_TIMER_SECONDS, None),
            "PERIOD=PREBATTLE")
    schedule_battle_period(sock, addr, sess)


def send_account_event(sock, addr, sess, msg_id: int, label: str,
                       extra: bytes = b''):
    """Р’С–РґРїСЂР°РІР»СЏС” Account entity-method (Р±РµР· args Р°Р±Рѕ Р· extra payload)."""
    em = struct.pack('<I', PLAYER_ENTITY_ID) + extra
    msg = msg_varlen(msg_id, em)
    send_lock = sess.setdefault('send_lock', threading.RLock())
    try:
        with send_lock:
            pkt = build_channel_packet(msg, sess, reliable=True)
            pkt = bw_encrypt_packet(pkt, sess['bf_key'])
            runtime_sendto(sock, pkt, addr)
    except Exception:
        return
    print(f"    [>] {label}  (msgID=0x{msg_id:02x})")

def send_account_server_counters(sock, addr, sess, label: str = "server counters"):
    send_avatar_messages(sock, addr, sess,
                         build_account_server_counters_update(),
                         label,
                         reliable=True)

def broadcast_account_server_counters(sock):
    for addr, sess in list(baseapp_clients.items()):
        if sess.get('init_sent'):
            send_account_server_counters(sock, addr, sess, '')


AVATAR_BASE_METHOD_VEHICLE_MOVE_WITH_IDS = {0xc0, 0xc3, 0xcc}
AVATAR_BASE_METHOD_VEHICLE_SHOOT_IDS = {0xc1, 0xc4, 0xcd}
AVATAR_BASE_METHOD_TRACK_POINT_WITH_GUN_IDS = {0x81, 0xce}
AVATAR_BASE_METHOD_STOP_TRACKING_WITH_GUN_IDS = {0xc2, 0xcf}
AVATAR_BASE_METHOD_CHANGE_SETTING_IDS = {0xd0}
AVATAR_BASE_METHOD_TELEPORT_IDS = {0xc5, 0xd1}
AVATAR_BASE_METHOD_USE_HORN_IDS = {0xc6, 0xd2}
AVATAR_BASE_METHOD_ON_CLIENT_READY_IDS = {0xc7, 0xd4}
AVATAR_BASE_METHOD_LEAVE_ARENA_IDS = {0xc8, 0xcb, 0xd6}
AVATAR_BASE_METHODS = (
    AVATAR_BASE_METHOD_VEHICLE_MOVE_WITH_IDS |
    AVATAR_BASE_METHOD_VEHICLE_SHOOT_IDS |
    AVATAR_BASE_METHOD_TRACK_POINT_WITH_GUN_IDS |
    AVATAR_BASE_METHOD_STOP_TRACKING_WITH_GUN_IDS |
    AVATAR_BASE_METHOD_CHANGE_SETTING_IDS |
    AVATAR_BASE_METHOD_TELEPORT_IDS |
    AVATAR_BASE_METHOD_USE_HORN_IDS |
    AVATAR_BASE_METHOD_ON_CLIENT_READY_IDS |
    AVATAR_BASE_METHOD_LEAVE_ARENA_IDS
)


def parse_exposed_request_id(payload: bytes):
    if len(payload) < 4:
        return None
    return struct.unpack_from('<I', payload, 0)[0]


def parse_vehicle_change_setting(payload: bytes):
    if len(payload) >= 9:
        return struct.unpack_from('<B', payload, 4)[0], struct.unpack_from('<i', payload, 5)[0]
    if len(payload) >= 5:
        return struct.unpack_from('<B', payload, 0)[0], struct.unpack_from('<i', payload, 1)[0]
    return None


def parse_vector3_exposed(payload: bytes):
    if len(payload) >= 16:
        return struct.unpack_from('<fff', payload, 4)
    if len(payload) >= 12:
        return struct.unpack_from('<fff', payload, 0)
    return None


def allocate_visual_shot_id(sess: dict) -> int:
    global next_visual_shot_id
    with battle_lock:
        shot_id = next_visual_shot_id
        next_visual_shot_id += 1
        if next_visual_shot_id > 16000000:
            next_visual_shot_id = 1
    sess['battle_last_visual_shot_id'] = shot_id
    return shot_id


def shot_dispersion_radius(shot_pos, target_pos) -> float:
    dx = float(target_pos[0]) - float(shot_pos[0])
    dy = float(target_pos[1]) - float(shot_pos[1])
    dz = float(target_pos[2]) - float(shot_pos[2])
    distance = math.sqrt(dx * dx + dy * dy + dz * dz)
    radius = distance / 100.0 * max(0.0, SHOT_DISPERSION_RADIUS_AT_100M)
    return clamp(radius, SHOT_DISPERSION_MIN_RADIUS,
                 SHOT_DISPERSION_MAX_RADIUS)


def shot_dispersion_effective_radius(shot_pos, target_pos) -> float:
    radius = shot_dispersion_radius(shot_pos, target_pos)
    return max(0.01, min(radius, radius * SHOT_DISPERSION_SERVER_RADIUS_SCALE))


def shot_dispersion_angle(shot_pos, target_pos) -> float:
    shot_pos = safe_vec3(shot_pos, None)
    target_pos = safe_vec3(target_pos, None)
    if shot_pos is None or target_pos is None:
        return 0.03
    dx = float(target_pos[0]) - float(shot_pos[0])
    dy = float(target_pos[1]) - float(shot_pos[1])
    dz = float(target_pos[2]) - float(shot_pos[2])
    distance = max(0.001, math.sqrt(dx * dx + dy * dy + dz * dz))
    radius = shot_dispersion_radius(shot_pos, target_pos)
    return max(0.001, math.atan(radius / distance))


def random_xz_offset(radius_min: float, radius_max: float,
                     center_bias: float = None):
    radius_min = max(0.0, float(radius_min))
    radius_max = max(radius_min, float(radius_max))
    center_bias = SHOT_DISPERSION_CENTER_BIAS if center_bias is None else center_bias
    center_bias = max(0.25, min(8.0, float(center_bias)))
    angle = random.uniform(0.0, math.pi * 2.0)
    fraction = clamp(random.uniform(0.0, 1.0), 0.0, 1.0) ** center_bias
    radius = radius_min + (radius_max - radius_min) * fraction
    return math.cos(angle) * radius, math.sin(angle) * radius


def apply_shot_dispersion(sess: dict, shot_pos, target_pos):
    sess['battle_last_shot_forced_miss'] = False
    target_pos = safe_vec3(target_pos, None)
    if target_pos is None:
        return None
    if not SHOT_DISPERSION_ENABLED or is_artillery_session(sess):
        return target_pos
    chance = clamp(SHOT_HIT_CHANCE_PERCENT / 100.0, 0.0, 1.0)
    radius = shot_dispersion_effective_radius(shot_pos, target_pos)
    if random.random() <= chance:
        offset_x, offset_z = random_xz_offset(
            0.0, min(radius, max(0.1, SHOT_TANK_HIT_RADIUS_H * 0.45)))
    else:
        miss_min = SHOT_TANK_HIT_RADIUS_H + 0.75
        if radius >= miss_min:
            offset_x, offset_z = random_xz_offset(miss_min, radius)
        else:
            sess['battle_last_shot_forced_miss'] = True
            offset_x, offset_z = random_xz_offset(radius * 0.85, radius)
    return (
        float(target_pos[0]) + offset_x,
        float(target_pos[1]),
        float(target_pos[2]) + offset_z,
    )


def direct_fire_target_point_from_gun(shot_pos, marker_pos, shot_vec):
    marker_pos = safe_vec3(marker_pos, None)
    shot_vec = normalize_vec(shot_vec)
    if marker_pos is not None:
        dx = float(marker_pos[0]) - float(shot_pos[0])
        dy = float(marker_pos[1]) - float(shot_pos[1])
        dz = float(marker_pos[2]) - float(shot_pos[2])
        distance = math.sqrt(dx * dx + dy * dy + dz * dz)
    else:
        distance = SHOT_TRACE_DISTANCE
    distance = clamp(distance, 1.0, SHOT_TRACE_DISTANCE)
    return (
        float(shot_pos[0]) + shot_vec[0] * distance,
        float(shot_pos[1]) + shot_vec[1] * distance,
        float(shot_pos[2]) + shot_vec[2] * distance,
    )


def direct_fire_marker_vec(shot_pos, marker_pos):
    marker_pos = safe_vec3(marker_pos, None)
    if marker_pos is None:
        return None
    dx = float(marker_pos[0]) - float(shot_pos[0])
    dy = float(marker_pos[1]) - float(shot_pos[1])
    dz = float(marker_pos[2]) - float(shot_pos[2])
    distance = math.sqrt(dx * dx + dy * dy + dz * dz)
    if distance <= 0.001:
        return None
    return (dx / distance, dy / distance, dz / distance)


def shot_vec_alignment_cosine(a, b):
    a = normalize_vec(a)
    b = normalize_vec(b)
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def shot_vec_from_angles(turret_yaw, gun_pitch):
    turret_yaw = safe_float(turret_yaw, 0.0)
    gun_pitch = safe_float(gun_pitch, 0.0)
    return normalize_vec((
        math.sin(turret_yaw) * math.cos(gun_pitch),
        math.sin(gun_pitch),
        math.cos(turret_yaw) * math.cos(gun_pitch),
    ))


def get_session_current_gun_direction(sess: dict):
    vehicle = get_session_battle_vehicle(sess)
    turret_yaw = (
        sess.get('battle_yaw', 0.0)
        if is_hull_locked_gun_vehicle(vehicle)
        else sess.get('battle_turret_yaw', sess.get('battle_yaw', 0.0)))
    return shot_vec_from_angles(
        turret_yaw,
        sess.get('battle_gun_pitch', 0.0))


def projectile_point(shot_pos, velocity, gravity, t):
    return (
        shot_pos[0] + velocity[0] * t,
        shot_pos[1] + velocity[1] * t - 0.5 * gravity * t * t,
        shot_pos[2] + velocity[2] * t,
    )


def trace_projectile_impact(sess: dict, shot_pos, shot_vec, speed, gravity,
                            shell: dict):
    shot_pos = safe_vec3(shot_pos, (0.0, 0.0, 0.0))
    shot_vec = normalize_vec(shot_vec)
    speed = safe_float(speed, 800.0, 0.001)
    gravity = safe_float(gravity, 9.81, 0.0)
    max_distance = safe_float((shell or {}).get('maxDistance'),
                              SHOT_TRACE_DISTANCE, 1.0,
                              SHOT_TRACE_DISTANCE)
    arena_type_id = int(sess.get('battle_arena_type_id') or ARENA_TYPE_KARELIA)
    velocity = (
        shot_vec[0] * speed,
        shot_vec[1] * speed,
        shot_vec[2] * speed,
    )
    max_time = min(30.0, max(1.0, max_distance / speed + 5.0))
    step = 0.05
    t = 0.0
    travelled = 0.0
    prev = shot_pos
    prev_terrain, _normal = sample_terrain(
        arena_type_id, prev[0], prev[2], prev[1])
    while t < max_time and travelled < max_distance:
        next_t = min(max_time, t + step)
        cur = projectile_point(shot_pos, velocity, gravity, next_t)
        segment = (cur[0] - prev[0], cur[1] - prev[1], cur[2] - prev[2])
        seg_len = math.sqrt(segment[0] * segment[0] +
                            segment[1] * segment[1] +
                            segment[2] * segment[2])
        if seg_len <= 0.0001:
            t = next_t
            continue
        if travelled + seg_len > max_distance:
            ratio = max(0.0, (max_distance - travelled) / seg_len)
            cur = (
                prev[0] + segment[0] * ratio,
                prev[1] + segment[1] * ratio,
                prev[2] + segment[2] * ratio,
            )
            segment = (cur[0] - prev[0], cur[1] - prev[1],
                       cur[2] - prev[2])
            seg_len = math.sqrt(segment[0] * segment[0] +
                                segment[1] * segment[1] +
                                segment[2] * segment[2])
            if seg_len <= 0.0001:
                return cur
        seg_dir = normalize_vec(segment)
        if ARTILLERY_OBSTACLE_BLOCKS_SHOT:
            shooter_gap = STATIC_OBSTACLE_SHOOTER_GAP if travelled <= 0.001 else 0.0
            obstacle = ray_static_obstacle_hit(
                sess, prev, seg_dir, seg_len,
                shooter_gap=shooter_gap, target_gap=0.0)
            if obstacle is not None:
                return (float(obstacle[1]), float(obstacle[2]),
                        float(obstacle[3]))
        cur_terrain, _normal = sample_terrain(
            arena_type_id, cur[0], cur[2], prev_terrain)
        prev_above = prev[1] - prev_terrain
        cur_above = cur[1] - cur_terrain
        if prev_above > 0.0 and cur_above <= 0.0:
            denom = prev_above - cur_above
            ratio = clamp(prev_above / denom if denom > 0.0001 else 1.0,
                          0.0, 1.0)
            hit_x = prev[0] + segment[0] * ratio
            hit_z = prev[2] + segment[2] * ratio
            hit_y, _normal = sample_terrain(
                arena_type_id, hit_x, hit_z, cur_terrain)
            return (hit_x, hit_y, hit_z)
        travelled += seg_len
        prev = cur
        prev_terrain = cur_terrain
        t = next_t
    return prev


def build_vehicle_shot_messages(sess: dict):
    sess['battle_last_shot_info'] = None
    sess['battle_last_server_shot_info'] = None
    sess['battle_last_server_shot_vec'] = None
    sess['battle_last_visual_flight_time'] = None
    vehicle = get_session_battle_vehicle(sess)
    shell_cd = int(sess.get('battle_current_shell') or 0)
    stock = sess.get('battle_ammo_stock') or build_vehicle_ammo_stock(vehicle)
    sess['battle_ammo_stock'] = stock
    shell_cd = select_available_shell(stock, shell_cd)
    if not shell_cd:
        shell = get_vehicle_shell(vehicle)
        shell_cd = int(shell.get('compactDescr', 0))
        sess['battle_current_shell'] = shell_cd
    if not shell_cd or int(stock.get(shell_cd, 0)) <= 0:
        return b'', 0.0, shell_cd, False
    reload_time = float(vehicle.get('reloadTime', 5.0))
    now = time.time()
    if now < float(sess.get('battle_reload_until', 0.0)):
        return b'', max(0.0, float(sess.get('battle_reload_until', 0.0)) - now), shell_cd, False
    stock[shell_cd] = max(0, int(stock.get(shell_cd, 0)) - 1)
    remaining = stock[shell_cd]
    shell = get_vehicle_shell(vehicle, shell_cd)
    speed = float(shell.get('speed', 800.0))
    gravity = float(shell.get('gravity', 9.81))
    effects_index = int(shell.get('effectsIndex', 0))
    high_arc = is_artillery_vehicle(vehicle)
    pos = get_effective_vehicle_pos(
        sess, sess.get('battle_pos', ARENA_SPAWN_POS[ARENA_TYPE_KARELIA]))
    shot_pos = (pos[0], pos[1] + 2.0, pos[2])
    if high_arc:
        sess['battle_last_shot_forced_miss'] = False
        marker_time = sess.get('battle_target_pos_time', 0.0)
        target_pos = None
        if is_target_marker_fresh(sess, marker_time):
            target_pos = normalize_artillery_target_pos(
                sess, sess.get('battle_target_pos'))
        if target_pos is not None:
            obstacle = marker_static_obstacle_hit(sess, shot_pos, target_pos)
            if obstacle is not None:
                target_pos = (
                    float(obstacle[1]),
                    float(obstacle[2]),
                    float(obstacle[3]),
                )
            server_shot_vec = ballistic_shot_vec(
                shot_pos, target_pos, speed, gravity, high_arc=True)
        else:
            server_shot_vec = get_session_current_gun_direction(sess)
            target_pos = trace_projectile_impact(
                sess, shot_pos, server_shot_vec, speed, gravity, shell)
        sess['battle_last_shot_target_pos'] = (
            tuple(float(v) for v in target_pos)
            if target_pos is not None else None)
        sess['battle_last_shot_target_pos_time'] = now
    else:
        base_shot_vec = get_session_current_gun_direction(sess)
        marker_time = sess.get('battle_target_pos_time', 0.0)
        marker_pos = sess.get('battle_target_pos')
        marker_shot_vec = (
            direct_fire_marker_vec(shot_pos, marker_pos)
            if is_target_marker_fresh(sess, marker_time)
            else None)
        if (marker_shot_vec is not None and
                shot_vec_alignment_cosine(base_shot_vec, marker_shot_vec) >=
                SHOT_GUN_ALIGNMENT_TOLERANCE_COS):
            aim_target_pos = safe_vec3(marker_pos, None)
        else:
            aim_target_pos = direct_fire_target_point_from_gun(
                shot_pos, marker_pos, base_shot_vec)
        target_pos = apply_shot_dispersion(sess, shot_pos, aim_target_pos)
        sess['battle_last_shot_target_pos'] = (
            tuple(float(v) for v in target_pos)
            if target_pos is not None else None)
        sess['battle_last_shot_target_pos_time'] = now
        if target_pos is not None:
            server_shot_vec = normalize_vec((
                float(target_pos[0]) - shot_pos[0],
                float(target_pos[1]) - shot_pos[1],
                float(target_pos[2]) - shot_pos[2],
            ))
        else:
            server_shot_vec = base_shot_vec
    server_velocity = (
        server_shot_vec[0] * speed,
        server_shot_vec[1] * speed,
        server_shot_vec[2] * speed)
    tracer_shot_pos = shot_pos
    tracer_velocity = server_velocity
    tracer_gravity = gravity
    tracer_shot_vec = server_shot_vec
    tracer_vehicle_id = PLAYER_VEHICLE_ID
    tracer_flight_time = None
    if high_arc and target_pos is not None:
        tracer_shot_pos, tracer_velocity, tracer_gravity, tracer_shot_vec, tracer_flight_time = (
            build_artillery_visible_tracer(shot_pos, target_pos,
                                           server_shot_vec, shell))
        tracer_vehicle_id = 0
        print(f"    [shot] art_tracer start=({tracer_shot_pos[0]:.1f},{tracer_shot_pos[1]:.1f},{tracer_shot_pos[2]:.1f}) vel=({tracer_velocity[0]:.1f},{tracer_velocity[1]:.1f},{tracer_velocity[2]:.1f}) vx={tracer_velocity[0]:.1f} impact=({target_pos[0]:.1f},{target_pos[1]:.1f},{target_pos[2]:.1f})")
    local_shot_id = int(sess.get('battle_shot_id', 1))
    sess['battle_shot_id'] = local_shot_id + 1
    shot_id = allocate_visual_shot_id(sess)
    sess['battle_shots'] = int(sess.get('battle_shots', 0)) + 1
    sess['battle_reload_until'] = now + reload_time
    sess['battle_last_shot_info'] = (
        shot_id, tracer_shot_pos, tracer_velocity, tracer_gravity, effects_index)
    sess['battle_last_server_shot_info'] = (
        shot_id, shot_pos, server_velocity, gravity, effects_index)
    sess['battle_last_shot_shell'] = shell
    sess['battle_last_shot_vec'] = tracer_shot_vec
    sess['battle_last_server_shot_vec'] = server_shot_vec
    sess['battle_last_visual_flight_time'] = tracer_flight_time
    msgs = b''
    msgs += build_vehicle_show_shooting()
    msgs += build_avatar_update_vehicle_ammo(shell_cd, remaining, 0)
    msgs += build_avatar_update_vehicle_reload(reload_time)
    msgs += build_avatar_show_tracer(shot_id, tracer_shot_pos,
                                     tracer_velocity, tracer_gravity,
                                     effects_index,
                                     vehicle_id=tracer_vehicle_id)
    return msgs, reload_time, shell_cd, True


def build_remote_vehicle_shot_messages(sess: dict, shot_id: int,
                                       shot_pos, velocity,
                                       gravity: float,
                                       effects_index: int,
                                       tracer_vehicle_id=None,
                                       include_shooting: bool = True) -> bytes:
    remote_id = get_remote_vehicle_id(sess)
    if tracer_vehicle_id is None:
        tracer_vehicle_id = remote_id
    msgs = b''
    if include_shooting:
        msgs += build_vehicle_show_shooting(remote_id)
    msgs += build_avatar_show_tracer(shot_id, shot_pos, velocity, gravity,
                                     effects_index, vehicle_id=tracer_vehicle_id)
    return msgs


def broadcast_remote_vehicle_shot(sock, source_sess: dict, shot_id: int,
                                  shot_pos, velocity,
                                  gravity: float,
                                  effects_index: int):
    mark_vehicle_shot_visibility_penalty(source_sess)
    ensure_shot_visual_viewers(source_sess, shot_id)
    source_account_id = source_sess.get('account_id')
    with battle_lock:
        observers = [other for account_id, other in active_battle_accounts.items()
                     if account_id != source_account_id and
                     other.get('battle_match_id') == source_sess.get('battle_match_id')]
    if not observers:
        return
    tracer_vehicle_id = 0 if is_artillery_session(source_sess) else None
    visible_msg = build_remote_vehicle_shot_messages(
        source_sess, shot_id, shot_pos, velocity, gravity, effects_index,
        tracer_vehicle_id, include_shooting=True)
    hidden_msg = b''
    if is_artillery_session(source_sess):
        hidden_msg = build_remote_vehicle_shot_messages(
            source_sess, shot_id, shot_pos, velocity, gravity, effects_index,
            0, include_shooting=False)

    def _send_visible_remote_shot(viewer, generation):
        if generation != viewer.get('battle_generation', 0):
            return
        if viewer.get('battle_match_id') != source_sess.get('battle_match_id'):
            return
        viewer_addr = viewer.get('addr')
        if not viewer_addr:
            return
        delay = remote_vehicle_intro_remaining_delay(viewer, source_sess)
        if delay > 0.0:
            runtime_call_later(delay,
                               lambda v=viewer, g=generation:
                                   _send_visible_remote_shot(v, g))
            return
        if not is_vehicle_visible_to(viewer, source_sess):
            return
        if send_avatar_messages(sock, viewer_addr, viewer,
                                visible_msg,
                                '', reliable=True):
            remember_shot_visual_viewer(source_sess, shot_id, viewer)

    for observer in observers:
        if not observer.get('addr'):
            continue
        known = observer.setdefault('known_remote_accounts', set())
        visible = is_vehicle_visible_to(observer, source_sess)
        if not visible and (
                shot_visibility_grace_active(observer, source_sess) or
                remote_vehicle_intro_remaining_delay(observer, source_sess) > 0.0 or
                keep_remote_entity_on_visibility_loss(observer, source_sess)):
            continue
        if not visible:
            hide_remote_vehicle(sock, observer, source_sess)
            if hidden_msg and send_avatar_messages(
                    sock, observer.get('addr'), observer,
                    hidden_msg, '', reliable=True):
                remember_shot_visual_viewer(source_sess, shot_id, observer)
            continue
        was_known = source_account_id in known
        if not was_known:
            outbound = build_remote_vehicle_messages(observer, source_sess)
            if not outbound:
                continue
            if send_avatar_messages(sock, observer.get('addr'), observer,
                                    outbound, '', reliable=True):
                known.add(source_account_id)
                mark_remote_vehicle_intro_sent(observer, source_sess)
                remember_remote_vehicle_in_arena(observer, source_sess)
                mark_remote_vehicle_spotted(observer, source_sess)
                generation = observer.get('battle_generation', 0)
                delay = max(REMOTE_SHOT_INTRO_DELAY,
                            remote_vehicle_intro_remaining_delay(observer,
                                                                 source_sess))
                runtime_call_later(delay,
                                   lambda v=observer, g=generation:
                                       _send_visible_remote_shot(v, g))
            continue
        intro_delay = remote_vehicle_intro_remaining_delay(observer, source_sess)
        if intro_delay > 0.0:
            generation = observer.get('battle_generation', 0)
            runtime_call_later(intro_delay,
                               lambda v=observer, g=generation:
                                   _send_visible_remote_shot(v, g))
            continue
        if send_avatar_messages(sock, observer.get('addr'), observer,
                                visible_msg, '', reliable=True):
            remember_shot_visual_viewer(source_sess, shot_id, observer)


def build_projectile_impact_messages(shot_id: int, effects_index: int,
                                     end_pos, velocity_dir) -> bytes:
    direction = normalize_vec(velocity_dir)
    return (
        build_avatar_stop_tracer(shot_id, end_pos) +
        build_avatar_explode_projectile(shot_id, effects_index, 0,
                                        end_pos, direction)
    )


def broadcast_projectile_impact(sock, source_sess: dict, shot_id: int,
                                effects_index: int, end_pos, velocity_dir):
    msg = build_projectile_impact_messages(shot_id, effects_index,
                                           end_pos, velocity_dir)
    match_id = source_sess.get('battle_match_id')
    visual_viewers = pop_shot_visual_viewers(source_sess, shot_id)
    with battle_lock:
        viewers = [sess for sess in active_battle_accounts.values()
                   if sess.get('battle_match_id') == match_id]
    for viewer in viewers:
        addr = viewer.get('addr')
        if not addr:
            continue
        if (visual_viewers is not None and
                viewer.get('account_id') not in visual_viewers):
            continue
        send_avatar_messages(sock, addr, viewer, msg, '', reliable=True)


def estimate_projectile_flight_time(shot_pos, impact_pos, velocity) -> float:
    shot_pos = safe_vec3(shot_pos, None)
    impact_pos = safe_vec3(impact_pos, None)
    velocity = safe_vec3(velocity, None)
    if shot_pos is None or impact_pos is None or velocity is None:
        return ARTILLERY_FLIGHT_TIME_MIN
    dx = impact_pos[0] - shot_pos[0]
    dy = impact_pos[1] - shot_pos[1]
    dz = impact_pos[2] - shot_pos[2]
    distance = math.sqrt(dx * dx + dy * dy + dz * dz)
    speed = math.sqrt(
        velocity[0] * velocity[0] +
        velocity[1] * velocity[1] +
        velocity[2] * velocity[2])
    if speed <= 0.001:
        return ARTILLERY_FLIGHT_TIME_MIN
    return clamp(distance / speed, ARTILLERY_FLIGHT_TIME_MIN,
                 ARTILLERY_FLIGHT_TIME_MAX)


def estimate_direct_projectile_impact_delay(shot_pos, impact_pos, velocity) -> float:
    shot_pos = safe_vec3(shot_pos, None)
    impact_pos = safe_vec3(impact_pos, None)
    velocity = safe_vec3(velocity, None)
    if shot_pos is None or impact_pos is None or velocity is None:
        return DIRECT_PROJECTILE_IMPACT_MIN_DELAY
    dx = impact_pos[0] - shot_pos[0]
    dy = impact_pos[1] - shot_pos[1]
    dz = impact_pos[2] - shot_pos[2]
    distance = math.sqrt(dx * dx + dy * dy + dz * dz)
    speed = math.sqrt(
        velocity[0] * velocity[0] +
        velocity[1] * velocity[1] +
        velocity[2] * velocity[2])
    if speed <= 0.001:
        return DIRECT_PROJECTILE_IMPACT_MIN_DELAY
    return clamp(distance / speed,
                 DIRECT_PROJECTILE_IMPACT_MIN_DELAY,
                 DIRECT_PROJECTILE_IMPACT_MAX_DELAY)


def get_session_shot_direction(sess: dict):
    shot_vec = sess.get('battle_shot_vec')
    if shot_vec is not None:
        return normalize_vec(shot_vec)
    return get_session_current_gun_direction(sess)


def unique_positions(points):
    out = []
    for point in points:
        pos = safe_vec3(point, None)
        if pos is None:
            continue
        if all(sum((pos[i] - other[i]) ** 2 for i in range(3)) > 0.01 for other in out):
            out.append(pos)
    return out


def ray_miss_distance(shot_pos, shot_vec, point):
    dx = point[0] - shot_pos[0]
    dy = point[1] - shot_pos[1]
    dz = point[2] - shot_pos[2]
    distance = dx * shot_vec[0] + dy * shot_vec[1] + dz * shot_vec[2]
    if distance < 0.0 or distance > SHOT_TRACE_DISTANCE:
        return None
    miss_x = dx - shot_vec[0] * distance
    miss_y = dy - shot_vec[1] * distance
    miss_z = dz - shot_vec[2] * distance
    return distance, math.sqrt(miss_x * miss_x + miss_y * miss_y + miss_z * miss_z)


def marker_vehicle_box_score(marker_pos, pos, yaw):
    dx = float(marker_pos[0]) - float(pos[0])
    dy = float(marker_pos[1]) - float(pos[1])
    dz = float(marker_pos[2]) - float(pos[2])
    sin_yaw = math.sin(float(yaw))
    cos_yaw = math.cos(float(yaw))
    local_x = dx * cos_yaw - dz * sin_yaw
    local_z = dx * sin_yaw + dz * cos_yaw
    if abs(local_x) > SHOT_TANK_HALF_WIDTH:
        return None
    if abs(local_z) > SHOT_TANK_HALF_LENGTH:
        return None
    if dy < SHOT_TANK_MIN_HEIGHT or dy > SHOT_TANK_MAX_HEIGHT:
        return None
    return abs(local_x) + abs(local_z) * 0.25 + abs(dy - SHOT_TANK_CENTER_HEIGHT) * 0.2


def signed_chunk_coord(value: str) -> int:
    coord = int(value, 16)
    return coord - 0x10000 if coord >= 0x8000 else coord


def unsigned_chunk_name(coord: int) -> str:
    return f"{int(coord) & 0xffff:04x}"


def decode_png_bytes(data: bytes):
    if not data.startswith(b'\x89PNG\r\n\x1a\n'):
        raise ValueError('bad png signature')
    pos = 8
    width = height = bit_depth = color_type = None
    idat = bytearray()
    while pos + 8 <= len(data):
        length = struct.unpack_from('>I', data, pos)[0]
        ctype = data[pos + 4:pos + 8]
        pos += 8
        chunk = data[pos:pos + length]
        pos += length + 4
        if ctype == b'IHDR':
            width, height, bit_depth, color_type, _comp, _flt, _interlace = struct.unpack('>IIBBBBB', chunk)
        elif ctype == b'IDAT':
            idat.extend(chunk)
        elif ctype == b'IEND':
            break
    if width is None or height is None:
        raise ValueError('png missing ihdr')
    if bit_depth != 8 or color_type != 6:
        raise ValueError(f'unsupported png format {bit_depth}/{color_type}')
    raw = zlib.decompress(bytes(idat))
    channels = 4
    stride = width * channels
    rows = []
    prev = bytearray(stride)
    idx = 0
    for _ in range(height):
        filter_type = raw[idx]
        idx += 1
        row = bytearray(raw[idx:idx + stride])
        idx += stride
        for i in range(stride):
            left = row[i - channels] if i >= channels else 0
            up = prev[i]
            up_left = prev[i - channels] if i >= channels else 0
            if filter_type == 1:
                row[i] = (row[i] + left) & 0xff
            elif filter_type == 2:
                row[i] = (row[i] + up) & 0xff
            elif filter_type == 3:
                row[i] = (row[i] + ((left + up) >> 1)) & 0xff
            elif filter_type == 4:
                p = left + up - up_left
                pa = abs(p - left)
                pb = abs(p - up)
                pc = abs(p - up_left)
                pr = left if pa <= pb and pa <= pc else up if pb <= pc else up_left
                row[i] = (row[i] + pr) & 0xff
            elif filter_type != 0:
                raise ValueError(f'unsupported png filter {filter_type}')
        rows.append(bytes(row))
        prev = row
    return width, height, b''.join(rows)


def decode_terrain2_heights(data: bytes):
    if len(data) < 36:
        raise ValueError('height block too small')
    magic, width, height, _compression, version, _min_height, _max_height, _pad = struct.unpack_from('<5I2fI', data, 0)
    if magic != 0x00706d68 or version != 4:
        raise ValueError(f'unsupported height map {magic:08x}/{version}')
    qpng = struct.unpack_from('<I', data, 32)[0]
    if qpng != 0x71706e67:
        raise ValueError(f'unsupported height compression {qpng:08x}')
    png_w, png_h, pixels = decode_png_bytes(data[36:])
    if png_w != width or png_h != height:
        raise ValueError(f'height size mismatch {png_w}x{png_h} != {width}x{height}')
    values = []
    for row in range(height):
        start = row * width * 4
        values.append([
            struct.unpack_from('<i', pixels, start + col * 4)[0] * 0.001
            for col in range(width)
        ])
    return values


class TerrainBlock2:
    def __init__(self, heights, chunk_x: int, chunk_z: int):
        self.heights = heights
        self.chunk_x = int(chunk_x)
        self.chunk_z = int(chunk_z)
        self.height_count = len(heights)
        self.width_count = len(heights[0]) if heights else 0
        self.visible_offset = BATTLE_TERRAIN_VISIBLE_OFFSET
        self.blocks_x = max(1, self.width_count - self.visible_offset * 2 - 1)
        self.blocks_z = max(1, self.height_count - self.visible_offset * 2 - 1)
        self.spacing_x = BATTLE_TERRAIN_CHUNK_SIZE / float(self.blocks_x)
        self.spacing_z = BATTLE_TERRAIN_CHUNK_SIZE / float(self.blocks_z)

    def _grid_height(self, x_idx: int, z_idx: int) -> float:
        x_idx = max(0, min(self.width_count - 1, int(x_idx)))
        z_idx = max(0, min(self.height_count - 1, int(z_idx)))
        return float(self.heights[z_idx][x_idx])

    def height_at_local(self, local_x: float, local_z: float) -> float:
        local_x = max(0.0, min(BATTLE_TERRAIN_CHUNK_SIZE, float(local_x)))
        local_z = max(0.0, min(BATTLE_TERRAIN_CHUNK_SIZE, float(local_z)))
        xs = local_x / self.spacing_x + self.visible_offset
        zs = local_z / self.spacing_z + self.visible_offset
        x_off = int(math.floor(xs))
        z_off = int(math.floor(zs))
        xf = xs - math.floor(xs)
        zf = zs - math.floor(zs)
        x_off = max(0, min(self.width_count - 2, x_off))
        z_off = max(0, min(self.height_count - 2, z_off))
        if (x_off ^ z_off) & 1:
            h01 = self._grid_height(x_off, z_off + 1)
            h10 = self._grid_height(x_off + 1, z_off)
            if (1.0 - xf) > zf:
                h00 = self._grid_height(x_off, z_off)
                return h00 + (h10 - h00) * xf + (h01 - h00) * zf
            h11 = self._grid_height(x_off + 1, z_off + 1)
            return h11 + (h01 - h11) * (1.0 - xf) + (h10 - h11) * (1.0 - zf)
        h00 = self._grid_height(x_off, z_off)
        h11 = self._grid_height(x_off + 1, z_off + 1)
        if xf > zf:
            h10 = self._grid_height(x_off + 1, z_off)
            return h10 + (h00 - h10) * (1.0 - xf) + (h11 - h10) * zf
        h01 = self._grid_height(x_off, z_off + 1)
        return h01 + (h11 - h01) * xf + (h00 - h01) * (1.0 - zf)

    def height_at_world(self, x: float, z: float) -> float:
        return self.height_at_local(
            float(x) - self.chunk_x * BATTLE_TERRAIN_CHUNK_SIZE,
            float(z) - self.chunk_z * BATTLE_TERRAIN_CHUNK_SIZE)


def load_terrain_block(arena_type_id: int, chunk_x: int, chunk_z: int):
    arena_type_id = normalize_arena_type_id(arena_type_id)
    cache_key = (arena_type_id, int(chunk_x), int(chunk_z))
    if cache_key in BATTLE_TERRAIN_BLOCK_CACHE:
        return BATTLE_TERRAIN_BLOCK_CACHE[cache_key]
    block = None
    space_dir = find_client_space_dir(arena_type_id)
    if space_dir:
        name = unsigned_chunk_name(chunk_x) + unsigned_chunk_name(chunk_z) + 'o.cdata'
        path = os.path.join(space_dir, name)
        try:
            with zipfile.ZipFile(path, 'r') as zf:
                heights = decode_terrain2_heights(zf.read('terrain2/heights'))
            block = TerrainBlock2(heights, chunk_x, chunk_z)
        except (OSError, KeyError, ValueError, zipfile.BadZipFile, zlib.error) as exc:
            warn_key = (arena_type_id, int(chunk_x), int(chunk_z), str(exc))
            if warn_key not in BATTLE_TERRAIN_WARNED:
                print(f"[!] terrain warning arena={arena_type_id} chunk=({chunk_x},{chunk_z}): {exc}")
                BATTLE_TERRAIN_WARNED.add(warn_key)
    else:
        warn_key = (arena_type_id, 'space_missing')
        if warn_key not in BATTLE_TERRAIN_WARNED:
            print(f"[!] terrain warning arena={arena_type_id}: client space dir not found")
            BATTLE_TERRAIN_WARNED.add(warn_key)
    BATTLE_TERRAIN_BLOCK_CACHE[cache_key] = block
    return block


def terrain_height_only(arena_type_id: int, x: float, z: float, fallback_y=None):
    chunk_x = math.floor(float(x) / BATTLE_TERRAIN_CHUNK_SIZE)
    chunk_z = math.floor(float(z) / BATTLE_TERRAIN_CHUNK_SIZE)
    block = load_terrain_block(arena_type_id, chunk_x, chunk_z)
    if block is None:
        return float(fallback_y if fallback_y is not None else 0.0)
    return block.height_at_world(x, z)


def sample_terrain(arena_type_id: int, x: float, z: float, fallback_y=None):
    height = terrain_height_only(arena_type_id, x, z, fallback_y)
    step = BATTLE_TERRAIN_NORMAL_STEP
    hx0 = terrain_height_only(arena_type_id, x - step, z, height)
    hx1 = terrain_height_only(arena_type_id, x + step, z, height)
    hz0 = terrain_height_only(arena_type_id, x, z - step, height)
    hz1 = terrain_height_only(arena_type_id, x, z + step, height)
    dhdx = (hx1 - hx0) / (2.0 * step)
    dhdz = (hz1 - hz0) / (2.0 * step)
    nx, ny, nz = -dhdx, 1.0, -dhdz
    length = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
    return height, (nx / length, ny / length, nz / length)


def find_client_space_dir(arena_type_id: int):
    geometry = ARENA_GEOMETRY_PATH.get(arena_type_id)
    if not geometry:
        return None
    relative = geometry.decode('ascii', 'ignore').strip('/').replace('/', os.sep)
    roots = get_client_roots()
    for root in roots:
        if not root:
            continue
        path = os.path.join(root, 'res', relative)
        if os.path.isdir(path):
            return path
    print('Checked roots:', roots, 'relative:', relative)
    return None


def find_client_res_file(resource_path: bytes):
    relative = resource_path.decode('ascii', 'ignore').strip('/').replace('/', os.sep)
    if relative.endswith('.modelx'):
        relative = relative[:-1]
    roots = get_client_roots()
    for root in roots:
        if not root:
            continue
        path = os.path.join(root, 'res', relative)
        if os.path.isfile(path):
            return path
    return None


def stone_obstacle_radius_fallback(model_path: bytes) -> float:
    name = model_path.lower()
    if b'/buildings/' in name or b'/buildingsrare/' in name:
        return 8.0
    if b'/millitaryinstallations/' in name:
        if b'pillbox' in name or b'bunker' in name or b'casemate' in name:
            return 6.0
        return 5.0
    if b'stones03' in name or b'stones04' in name:
        return 6.0
    if b'stones01' in name or b'stones02' in name or b'stones05' in name:
        return 5.5
    if b'stones5' in name:
        return 4.0
    return 3.0


def fallback_model_obstacle_bounds(model_path: bytes):
    radius = stone_obstacle_radius_fallback(model_path)
    return (-radius, -radius * 0.5, -radius, radius, radius, radius)


_PACKED_SECTION_MAGIC = 0x62a14e45
_PACKED_SECTION_DATA_POS_MASK = 0x0FFFFFFF
_PACKED_SECTION_TYPE_SHIFT = 28
_PACKED_SECTION_TYPE_SECTION = 0
_PACKED_SECTION_TYPE_FLOAT = 3


def _read_packed_section_strings(data: bytes):
    if len(data) < 5:
        return None, 0
    try:
        magic, = struct.unpack_from('<I', data, 0)
    except struct.error:
        return None, 0
    if magic != _PACKED_SECTION_MAGIC:
        return None, 0
    offset = 5
    strings = []
    while True:
        end = data.find(b'\x00', offset)
        if end < 0:
            return None, 0
        name = data[offset:end]
        offset = end + 1
        if not name:
            return strings, offset
        strings.append(name)


def _walk_packed_section_children(data: bytes, sec_off: int, strings):
    if sec_off + 2 > len(data):
        return None
    try:
        num_children = struct.unpack_from('<h', data, sec_off)[0]
    except struct.error:
        return None
    if num_children < 0:
        return None
    rec_off = sec_off + 2
    final_rec = rec_off + num_children * 6
    if final_rec + 4 > len(data):
        return None
    records = []
    for i in range(num_children):
        try:
            dp_raw, kp = struct.unpack_from('<iH', data, rec_off + i * 6)
        except struct.error:
            return None
        records.append((dp_raw, kp))
    try:
        final_dp_raw = struct.unpack_from('<i', data, final_rec)[0]
    except struct.error:
        return None
    own_dp_raw = records[0][0] if num_children > 0 else final_dp_raw
    own_endpos = own_dp_raw & _PACKED_SECTION_DATA_POS_MASK
    block_data_start = final_rec + 4
    prev_end = own_endpos
    children = []
    for i in range(num_children):
        ep_raw = records[i + 1][0] if i + 1 < num_children else final_dp_raw
        endpos = ep_raw & _PACKED_SECTION_DATA_POS_MASK
        ctype = (ep_raw >> _PACKED_SECTION_TYPE_SHIFT) & 0xF
        keypos = records[i][1]
        name = strings[keypos] if 0 <= keypos < len(strings) else b''
        children.append({
            'name': name,
            'type': ctype,
            'data_off': block_data_start + prev_end,
            'data_size': max(0, endpos - prev_end),
        })
        prev_end = endpos
    return children


def _read_packed_section_min_max_floats(data: bytes, section):
    children = _walk_packed_section_children(data, section['data_off'], section['_strings'])
    if children is None:
        return None
    min_vec = max_vec = None
    for child in children:
        if (child['type'] != _PACKED_SECTION_TYPE_FLOAT or
                child['data_size'] < 12):
            continue
        try:
            floats = struct.unpack_from('<3f', data, child['data_off'])
        except struct.error:
            continue
        if not all(math.isfinite(v) for v in floats):
            continue
        if child['name'] == b'min':
            min_vec = floats
        elif child['name'] == b'max':
            max_vec = floats
    if min_vec is None or max_vec is None:
        return None
    return (min_vec[0], min_vec[1], min_vec[2],
            max_vec[0], max_vec[1], max_vec[2])


def _packed_section_find_bounds(data: bytes):
    strings, root_off = _read_packed_section_strings(data)
    if strings is None or root_off <= 0:
        return None
    targets = {b'visibilityBox', b'boundingBox'}

    def visit(sec_off):
        children = _walk_packed_section_children(data, sec_off, strings)
        if children is None:
            return None
        for child in children:
            if (child['type'] == _PACKED_SECTION_TYPE_SECTION and
                    child['name'] in targets):
                bounds = _read_packed_section_min_max_floats(
                    data, dict(child, _strings=strings))
                if bounds is not None:
                    return bounds
        for child in children:
            if (child['type'] == _PACKED_SECTION_TYPE_SECTION and
                    child['data_size'] >= 2):
                bounds = visit(child['data_off'])
                if bounds is not None:
                    return bounds
        return None

    return visit(root_off)


def read_model_obstacle_bounds(model_path: bytes):
    cache_key = model_path.lower()
    if cache_key in STATIC_MODEL_BOUNDS_CACHE:
        return STATIC_MODEL_BOUNDS_CACHE[cache_key]
    bounds = None
    path = find_client_res_file(model_path)
    if path:
        try:
            with open(path, 'rb') as fh:
                data = fh.read()
        except OSError:
            data = None
        if data:
            bounds = _packed_section_find_bounds(data)
            if bounds is None:
                for offset in range(0, max(0, len(data) - 24 + 1)):
                    values = struct.unpack_from('<6f', data, offset)
                    if not all(math.isfinite(v) for v in values):
                        continue
                    min_x, min_y, min_z, max_x, max_y, max_z = values
                    if (-40.0 < min_x < 0.0 and -20.0 < min_y < 10.0 and
                            -40.0 < min_z < 0.0 and 0.0 < max_x < 40.0 and
                            0.0 < max_y < 20.0 and 0.0 < max_z < 40.0):
                        bounds = values
                        break
    if bounds is None:
        bounds = fallback_model_obstacle_bounds(model_path)
    STATIC_MODEL_BOUNDS_CACHE[cache_key] = bounds
    return bounds


def model_obstacle_bounds_radius(bounds) -> float:
    min_x, _min_y, min_z, max_x, _max_y, max_z = bounds
    return math.sqrt(
        max(abs(min_x), abs(max_x)) ** 2 +
        max(abs(min_z), abs(max_z)) ** 2)


def read_model_obstacle_radius(model_path: bytes):
    cache_key = model_path.lower()
    if cache_key in STATIC_MODEL_RADIUS_CACHE:
        return STATIC_MODEL_RADIUS_CACHE[cache_key]
    radius = model_obstacle_bounds_radius(read_model_obstacle_bounds(model_path))
    STATIC_MODEL_RADIUS_CACHE[cache_key] = radius
    return radius


def stone_obstacle_radius(model_path: bytes) -> float:
    return read_model_obstacle_radius(model_path)


def movement_obstacle_radius(visual_radius: float) -> float:
    return max(
        STATIC_OBSTACLE_MOVE_RADIUS_MIN,
        min(STATIC_OBSTACLE_MOVE_RADIUS_MAX,
            float(visual_radius) * STATIC_OBSTACLE_MOVE_RADIUS_FACTOR))


def get_obstacle_move_radius(obstacle) -> float:
    return float(obstacle[3])


def get_obstacle_shot_radius(obstacle) -> float:
    if len(obstacle) >= 5:
        return float(obstacle[4])
    return float(obstacle[3])


def get_obstacle_move_footprint(obstacle):
    if len(obstacle) >= 7:
        return obstacle[6]
    return None


def transform_model_point(transform, point):
    px, py, pz = point
    return (
        px * transform[0] + py * transform[3] + pz * transform[6] + transform[9],
        px * transform[1] + py * transform[4] + pz * transform[7] + transform[10],
        px * transform[2] + py * transform[5] + pz * transform[8] + transform[11],
    )


def convex_hull_xz(points):
    pts = sorted(set((round(float(x), 6), round(float(z), 6)) for x, z in points))
    if len(pts) <= 2:
        return tuple(pts)

    def cross(origin, a, b):
        return ((a[0] - origin[0]) * (b[1] - origin[1]) -
                (a[1] - origin[1]) * (b[0] - origin[0]))

    lower = []
    for point in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0.0:
            lower.pop()
        lower.append(point)
    upper = []
    for point in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0.0:
            upper.pop()
        upper.append(point)
    return tuple(lower[:-1] + upper[:-1])


def build_model_footprint(chunk_x: int, chunk_z: int, transform, bounds):
    min_x, min_y, min_z, max_x, max_y, max_z = bounds
    origin_x = chunk_x * BATTLE_TERRAIN_CHUNK_SIZE
    origin_z = chunk_z * BATTLE_TERRAIN_CHUNK_SIZE
    points = []
    for px in (min_x, max_x):
        for py in (min_y, max_y):
            for pz in (min_z, max_z):
                tx, _ty, tz = transform_model_point(transform, (px, py, pz))
                points.append((origin_x + tx, origin_z + tz))
    return convex_hull_xz(points)


def scaled_footprint(points, center_x: float, center_z: float, factor: float):
    factor = max(0.0, float(factor))
    return tuple(
        (center_x + (float(x) - center_x) * factor,
         center_z + (float(z) - center_z) * factor)
        for x, z in points)


def transformed_bounds_radius(center_x: float, center_z: float, footprint) -> float:
    radius = 0.0
    for x, z in footprint or ():
        dx = float(x) - center_x
        dz = float(z) - center_z
        radius = max(radius, math.sqrt(dx * dx + dz * dz))
    return radius


def point_in_polygon_xz(x: float, z: float, polygon) -> bool:
    inside = False
    count = len(polygon or ())
    if count < 3:
        return False
    j = count - 1
    for i in range(count):
        xi, zi = polygon[i]
        xj, zj = polygon[j]
        if ((zi > z) != (zj > z) and
                x < (xj - xi) * (z - zi) / ((zj - zi) or 0.000001) + xi):
            inside = not inside
        j = i
    return inside


def point_segment_distance_sq_xz(px: float, pz: float, ax: float, az: float,
                                 bx: float, bz: float) -> float:
    dx = bx - ax
    dz = bz - az
    length_sq = dx * dx + dz * dz
    if length_sq <= 0.000001:
        ox = px - ax
        oz = pz - az
        return ox * ox + oz * oz
    t = ((px - ax) * dx + (pz - az) * dz) / length_sq
    t = max(0.0, min(1.0, t))
    cx = ax + dx * t
    cz = az + dz * t
    ox = px - cx
    oz = pz - cz
    return ox * ox + oz * oz


def point_hits_footprint(x: float, z: float, footprint, tank_radius: float) -> bool:
    if not footprint:
        return False
    if point_in_polygon_xz(x, z, footprint):
        return True
    radius_sq = max(0.0, float(tank_radius)) ** 2
    count = len(footprint)
    for i in range(count):
        ax, az = footprint[i]
        bx, bz = footprint[(i + 1) % count]
        if point_segment_distance_sq_xz(x, z, ax, az, bx, bz) <= radius_sq:
            return True
    return False


def segment_orientation_xz(ax: float, az: float, bx: float, bz: float,
                           cx: float, cz: float) -> float:
    return (bx - ax) * (cz - az) - (bz - az) * (cx - ax)


def point_on_segment_xz(px: float, pz: float, ax: float, az: float,
                        bx: float, bz: float) -> bool:
    return (min(ax, bx) - 0.000001 <= px <= max(ax, bx) + 0.000001 and
            min(az, bz) - 0.000001 <= pz <= max(az, bz) + 0.000001 and
            abs(segment_orientation_xz(ax, az, bx, bz, px, pz)) <= 0.000001)


def segments_intersect_xz(ax: float, az: float, bx: float, bz: float,
                          cx: float, cz: float, dx: float, dz: float) -> bool:
    o1 = segment_orientation_xz(ax, az, bx, bz, cx, cz)
    o2 = segment_orientation_xz(ax, az, bx, bz, dx, dz)
    o3 = segment_orientation_xz(cx, cz, dx, dz, ax, az)
    o4 = segment_orientation_xz(cx, cz, dx, dz, bx, bz)
    if ((o1 > 0.0 and o2 < 0.0) or (o1 < 0.0 and o2 > 0.0)) and \
            ((o3 > 0.0 and o4 < 0.0) or (o3 < 0.0 and o4 > 0.0)):
        return True
    if abs(o1) <= 0.000001 and point_on_segment_xz(cx, cz, ax, az, bx, bz):
        return True
    if abs(o2) <= 0.000001 and point_on_segment_xz(dx, dz, ax, az, bx, bz):
        return True
    if abs(o3) <= 0.000001 and point_on_segment_xz(ax, az, cx, cz, dx, dz):
        return True
    if abs(o4) <= 0.000001 and point_on_segment_xz(bx, bz, cx, cz, dx, dz):
        return True
    return False


def segment_segment_distance_sq_xz(ax: float, az: float, bx: float, bz: float,
                                   cx: float, cz: float, dx: float, dz: float) -> float:
    if segments_intersect_xz(ax, az, bx, bz, cx, cz, dx, dz):
        return 0.0
    return min(
        point_segment_distance_sq_xz(ax, az, cx, cz, dx, dz),
        point_segment_distance_sq_xz(bx, bz, cx, cz, dx, dz),
        point_segment_distance_sq_xz(cx, cz, ax, az, bx, bz),
        point_segment_distance_sq_xz(dx, dz, ax, az, bx, bz),
    )


def segment_hits_footprint(prev_x: float, prev_z: float, new_x: float,
                           new_z: float, footprint, tank_radius: float) -> bool:
    if not footprint:
        return False
    if point_hits_footprint(prev_x, prev_z, footprint, tank_radius):
        return True
    if point_hits_footprint(new_x, new_z, footprint, tank_radius):
        return True
    radius_sq = max(0.0, float(tank_radius)) ** 2
    count = len(footprint)
    for i in range(count):
        ax, az = footprint[i]
        bx, bz = footprint[(i + 1) % count]
        if segments_intersect_xz(prev_x, prev_z, new_x, new_z, ax, az, bx, bz):
            return True
        if segment_segment_distance_sq_xz(
                prev_x, prev_z, new_x, new_z, ax, az, bx, bz) <= radius_sq:
            return True
    return False


_STATIC_OBSTACLE_PREFIXES = (
    b'content/buildings/',
    b'content/buildingsrare/',
    b'content/millitaryinstallations/',
)

_STATIC_OBSTACLE_ENV_KEYWORDS = (
    b'stone', b'rock', b'memorial', b'snagheap',
)


_STATIC_OBSTACLE_EXCLUDE_KEYWORDS = (
    b'tree', b'stump', b'bush', b'fence', b'log',
    b'dead', b'broken', b'debris', b'fallen', b'trunk', b'branch', b'snag', b'wood',
)


def is_static_obstacle_model(model_path: bytes) -> bool:
    name = (model_path or b'').lower()
    if not (name.endswith(b'.model') or name.endswith(b'.modelx')):
        return False
    if b'/lod1/' in name or b'/lod2/' in name:
        return False
    for keyword in _STATIC_OBSTACLE_EXCLUDE_KEYWORDS:
        if keyword in name:
            return False
    for prefix in _STATIC_OBSTACLE_PREFIXES:
        if name.startswith(prefix):
            return True
    if name.startswith(b'content/environment/'):
        for keyword in _STATIC_OBSTACLE_ENV_KEYWORDS:
            if keyword in name:
                return True
    return False


def static_obstacle_exclusion_zones(arena_type_id: int):
    zones = get_value(CONFIG, 'maps.static_obstacle_exclusions', {})
    if isinstance(zones, dict):
        zones = zones.get(str(int(arena_type_id))) or zones.get(int(arena_type_id)) or []
    if not isinstance(zones, list):
        return []
    return zones


def is_static_obstacle_excluded(arena_type_id: int, x: float, z: float,
                                model_path: bytes) -> bool:
    model_text = (model_path or b'').decode('ascii', 'ignore').lower()
    for zone in static_obstacle_exclusion_zones(arena_type_id):
        if not isinstance(zone, dict):
            continue
        center = zone.get('center')
        if not isinstance(center, list) or len(center) < 2:
            continue
        try:
            cx = float(center[0])
            cz = float(center[1])
            radius = float(zone.get('radius', 0.0))
        except (TypeError, ValueError):
            continue
        if radius <= 0.0:
            continue
        model_filter = str(zone.get('model', '') or '').lower()
        if model_filter and model_filter not in model_text:
            continue
        dx = float(x) - cx
        dz = float(z) - cz
        if dx * dx + dz * dz <= radius * radius:
            return True
    return False


def read_chunk_model_transform(data: bytes, offset: int):
    if offset + 48 > len(data):
        return None
    try:
        values = struct.unpack_from('<12f', data, offset)
    except struct.error:
        return None
    if not all(math.isfinite(v) for v in values):
        return None
    axes = (values[0:3], values[3:6], values[6:9])
    axis_lengths = []
    for axis in axes:
        length = math.sqrt(axis[0] * axis[0] + axis[1] * axis[1] + axis[2] * axis[2])
        if length < 0.05 or length > 20.0:
            return None
        axis_lengths.append(length)
    local_x, local_y, local_z = values[9], values[10], values[11]
    if not (-1000.0 <= local_x <= 1000.0 and
            -500.0 <= local_y <= 500.0 and
            -1000.0 <= local_z <= 1000.0):
        return None
    return values


def iter_static_model_instances_from_chunk(data: bytes, ignored=None):
    for match in STATIC_OBSTACLE_MODEL_RE.finditer(data or b''):
        model_path = match.group()
        if not is_static_obstacle_model(model_path):
            if ignored is not None:
                ignored['resource'] = int(ignored.get('resource', 0)) + 1
            continue
        transform = None
        for add in (1, 0):
            transform = read_chunk_model_transform(data, match.end() + add)
            if transform is not None:
                break
        if transform is None:
            if ignored is not None:
                ignored['transform'] = int(ignored.get('transform', 0)) + 1
            continue
        yield model_path, transform


def validate_static_obstacle_instance(arena_type_id: int, chunk_x: int,
                                      chunk_z: int, model_path: bytes,
                                      transform, ignored=None):
    if find_client_res_file(model_path) is None:
        if ignored is not None:
            ignored['missing_model'] = int(ignored.get('missing_model', 0)) + 1
    local_x, local_y, local_z = transform[9], transform[10], transform[11]
    margin = STATIC_OBSTACLE_CHUNK_MARGIN
    if not (-margin <= local_x <= BATTLE_TERRAIN_CHUNK_SIZE + margin and
            -margin <= local_z <= BATTLE_TERRAIN_CHUNK_SIZE + margin):
        if ignored is not None:
            ignored['chunk_bounds'] = int(ignored.get('chunk_bounds', 0)) + 1
        return None
    x = chunk_x * BATTLE_TERRAIN_CHUNK_SIZE + local_x
    z = chunk_z * BATTLE_TERRAIN_CHUNK_SIZE + local_z
    if is_static_obstacle_excluded(arena_type_id, x, z, model_path):
        if ignored is not None:
            ignored['excluded'] = int(ignored.get('excluded', 0)) + 1
        return None
    terrain_y = terrain_height_only(arena_type_id, x, z, local_y)
    if abs(local_y - terrain_y) > STATIC_OBSTACLE_TERRAIN_Y_TOLERANCE:
        if ignored is not None:
            ignored['terrain_y'] = int(ignored.get('terrain_y', 0)) + 1
        return None
    bounds = read_model_obstacle_bounds(model_path)
    footprint = build_model_footprint(chunk_x, chunk_z, transform, bounds)
    transformed_radius = transformed_bounds_radius(x, z, footprint)
    if transformed_radius <= 0.0:
        transformed_radius = stone_obstacle_radius(model_path)
    visual_radius = transformed_radius + STATIC_OBSTACLE_SHOT_RADIUS_PAD
    move_radius = movement_obstacle_radius(visual_radius)
    if (footprint and len(footprint) >= 3 and
            STATIC_OBSTACLE_FOOTPRINT_SHRINK < 1.0):
        footprint = scaled_footprint(
            footprint, x, z, STATIC_OBSTACLE_FOOTPRINT_SHRINK)
    return (x, local_y, z, move_radius, visual_radius, model_path, footprint)


def build_static_obstacles_from_chunk_data(arena_type_id: int, chunk_x: int,
                                           chunk_z: int, data: bytes,
                                           ignored=None):
    obstacles = []
    seen = set()
    for model_path, transform in iter_static_model_instances_from_chunk(data, ignored):
        obstacle = validate_static_obstacle_instance(
            arena_type_id, chunk_x, chunk_z, model_path, transform, ignored)
        if obstacle is None:
            continue
        key = (
            model_path.lower(),
            round(obstacle[0], 2),
            round(obstacle[1], 2),
            round(obstacle[2], 2),
        )
        if key in seen:
            if ignored is not None:
                ignored['duplicate'] = int(ignored.get('duplicate', 0)) + 1
            continue
        seen.add(key)
        obstacles.append(obstacle)
    return obstacles


def load_static_obstacles_for_arena(arena_type_id: int):
    try:
        raw_arena_type_id = int(arena_type_id)
    except (TypeError, ValueError):
        raw_arena_type_id = None
    if raw_arena_type_id in STATIC_OBSTACLE_CACHE:
        return STATIC_OBSTACLE_CACHE[raw_arena_type_id]
    arena_type_id = normalize_arena_type_id(arena_type_id)
    if arena_type_id in STATIC_OBSTACLE_CACHE:
        return STATIC_OBSTACLE_CACHE[arena_type_id]
    obstacles = []
    ignored = {}
    seen = set()
    space_dir = find_client_space_dir(arena_type_id)
    if space_dir:
        for name in os.listdir(space_dir):
            if not name.endswith('.chunk') or len(name) < 14:
                continue
            stem = os.path.splitext(name)[0]
            if len(stem) < 9 or not stem.endswith('o'):
                continue
            try:
                chunk_x = signed_chunk_coord(stem[:4])
                chunk_z = signed_chunk_coord(stem[4:8])
                with open(os.path.join(space_dir, name), 'rb') as fh:
                    data = fh.read()
            except (OSError, ValueError):
                ignored['read'] = int(ignored.get('read', 0)) + 1
                continue
            for obstacle in build_static_obstacles_from_chunk_data(
                    arena_type_id, chunk_x, chunk_z, data, ignored):
                key = (
                    obstacle[5].lower(),
                    round(obstacle[0], 2),
                    round(obstacle[1], 2),
                    round(obstacle[2], 2),
                )
                if key in seen:
                    ignored['duplicate'] = int(ignored.get('duplicate', 0)) + 1
                    continue
                seen.add(key)
                obstacles.append(obstacle)
    STATIC_OBSTACLE_CACHE[arena_type_id] = obstacles
    STATIC_OBSTACLE_INDEX_CACHE.pop(arena_type_id, None)
    if obstacles or ignored:
        ignored_text = ','.join(f"{k}={v}" for k, v in sorted(ignored.items())) or 'none'
        print(f"    [battle] loaded {len(obstacles)} static obstacle(s) for arenaType={arena_type_id} ignored={ignored_text}")
    return obstacles


def obstacle_xz_bbox(obstacle):
    ox = float(obstacle[0])
    oz = float(obstacle[2])
    footprint = get_obstacle_move_footprint(obstacle)
    if footprint:
        xs = [float(px) for px, _pz in footprint]
        zs = [float(pz) for _px, pz in footprint]
        return min(xs), min(zs), max(xs), max(zs)
    r = float(get_obstacle_move_radius(obstacle))
    return ox - r, oz - r, ox + r, oz + r


def build_static_obstacle_index(arena_type_id: int, obstacles):
    cell = STATIC_OBSTACLE_INDEX_CELL
    inv = 1.0 / cell
    grid = {}
    for obstacle in obstacles:
        min_x, min_z, max_x, max_z = obstacle_xz_bbox(obstacle)
        cx0 = int(math.floor(min_x * inv))
        cz0 = int(math.floor(min_z * inv))
        cx1 = int(math.floor(max_x * inv))
        cz1 = int(math.floor(max_z * inv))
        for cx in range(cx0, cx1 + 1):
            for cz in range(cz0, cz1 + 1):
                grid.setdefault((cx, cz), []).append(obstacle)
    entry = (cell, grid, obstacles)
    STATIC_OBSTACLE_INDEX_CACHE[arena_type_id] = entry
    return entry


def get_static_obstacle_index(arena_type_id: int):
    obstacles = STATIC_OBSTACLE_CACHE.get(arena_type_id)
    if obstacles is None:
        return None
    cached = STATIC_OBSTACLE_INDEX_CACHE.get(arena_type_id)
    if cached is not None and cached[2] is obstacles:
        return cached
    return build_static_obstacle_index(arena_type_id, obstacles)


def is_spotting_bush_path(path: bytes) -> bool:
    name = (path or b'').lower()
    return any(pattern and pattern in name for pattern in SPOTTING_BUSH_PATTERNS)


def read_speedtree_transform(data: bytes, offset: int):
    for add in (1, 0):
        transform = read_chunk_model_transform(data, offset + add)
        if transform is not None:
            return transform
    return None


def iter_spotting_bush_instances_from_chunk(data: bytes):
    for match in SPOTTING_BUSH_RE.finditer(data or b''):
        path = match.group()
        if not is_spotting_bush_path(path):
            continue
        transform = read_speedtree_transform(data, match.end())
        if transform is not None:
            yield path, transform


def build_spotting_bushes_from_chunk_data(chunk_x: int, chunk_z: int,
                                          data: bytes):
    bushes = []
    origin_x = chunk_x * BATTLE_TERRAIN_CHUNK_SIZE
    origin_z = chunk_z * BATTLE_TERRAIN_CHUNK_SIZE
    for path, transform in iter_spotting_bush_instances_from_chunk(data):
        local_x, local_z = transform[9], transform[11]
        margin = STATIC_OBSTACLE_CHUNK_MARGIN
        if not (-margin <= local_x <= BATTLE_TERRAIN_CHUNK_SIZE + margin and
                -margin <= local_z <= BATTLE_TERRAIN_CHUNK_SIZE + margin):
            continue
        x = origin_x + local_x
        z = origin_z + local_z
        scale_x = math.sqrt(sum(v * v for v in transform[0:3]))
        scale_z = math.sqrt(sum(v * v for v in transform[6:9]))
        scale = clamp((scale_x + scale_z) * 0.5, 0.5, 3.0)
        radius = SPOTTING_BUSH_RADIUS * scale
        bushes.append((x, z, radius, path))
    return bushes


def load_spotting_bushes_for_arena(arena_type_id: int):
    arena_type_id = normalize_arena_type_id(arena_type_id)
    if arena_type_id in SPOTTING_BUSH_CACHE:
        return SPOTTING_BUSH_CACHE[arena_type_id]
    bushes = []
    seen = set()
    space_dir = find_client_space_dir(arena_type_id)
    if space_dir:
        for name in os.listdir(space_dir):
            if not name.endswith('.chunk') or len(name) < 14:
                continue
            stem = os.path.splitext(name)[0]
            if len(stem) < 9 or not stem.endswith('o'):
                continue
            try:
                chunk_x = signed_chunk_coord(stem[:4])
                chunk_z = signed_chunk_coord(stem[4:8])
                with open(os.path.join(space_dir, name), 'rb') as fh:
                    data = fh.read()
            except (OSError, ValueError):
                continue
            for bush in build_spotting_bushes_from_chunk_data(
                    chunk_x, chunk_z, data):
                key = (round(bush[0], 2), round(bush[1], 2), bush[3].lower())
                if key in seen:
                    continue
                seen.add(key)
                bushes.append(bush)
    SPOTTING_BUSH_CACHE[arena_type_id] = bushes
    SPOTTING_BUSH_INDEX_CACHE.pop(arena_type_id, None)
    if bushes:
        print(f"    [battle] loaded {len(bushes)} spotting bush(es) for arenaType={arena_type_id}")
    return bushes


def build_spotting_bush_index(arena_type_id: int, bushes):
    cell = STATIC_OBSTACLE_INDEX_CELL
    inv = 1.0 / cell
    grid = {}
    for bush in bushes:
        x, z, radius, _path = bush
        cx0 = int(math.floor((x - radius) * inv))
        cz0 = int(math.floor((z - radius) * inv))
        cx1 = int(math.floor((x + radius) * inv))
        cz1 = int(math.floor((z + radius) * inv))
        for cx in range(cx0, cx1 + 1):
            for cz in range(cz0, cz1 + 1):
                grid.setdefault((cx, cz), []).append(bush)
    entry = (cell, grid, bushes)
    SPOTTING_BUSH_INDEX_CACHE[arena_type_id] = entry
    return entry


def get_spotting_bush_index(arena_type_id: int):
    bushes = SPOTTING_BUSH_CACHE.get(arena_type_id)
    if bushes is None:
        return None
    cached = SPOTTING_BUSH_INDEX_CACHE.get(arena_type_id)
    if cached is not None and cached[2] is bushes:
        return cached
    return build_spotting_bush_index(arena_type_id, bushes)


def iter_spotting_bushes_near_segment(arena_type_id: int,
                                      ax: float, az: float,
                                      bx: float, bz: float,
                                      halo: float = 0.0):
    bushes = load_spotting_bushes_for_arena(arena_type_id)
    if not bushes:
        return
    index = get_spotting_bush_index(arena_type_id)
    if index is None:
        for bush in bushes:
            yield bush
        return
    cell, grid, _ref = index
    inv = 1.0 / cell
    pad = max(0.0, float(halo))
    min_x = min(ax, bx) - pad
    max_x = max(ax, bx) + pad
    min_z = min(az, bz) - pad
    max_z = max(az, bz) + pad
    cx0 = int(math.floor(min_x * inv))
    cz0 = int(math.floor(min_z * inv))
    cx1 = int(math.floor(max_x * inv))
    cz1 = int(math.floor(max_z * inv))
    seen = set()
    for cx in range(cx0, cx1 + 1):
        for cz in range(cz0, cz1 + 1):
            bucket = grid.get((cx, cz))
            if not bucket:
                continue
            for bush in bucket:
                key = id(bush)
                if key in seen:
                    continue
                seen.add(key)
                yield bush


def iter_obstacles_near_point(arena_type_id: int, x: float, z: float,
                              halo: float = 0.0):
    obstacles = load_static_obstacles_for_arena(arena_type_id)
    if not obstacles:
        return
    index = get_static_obstacle_index(arena_type_id)
    if index is None:
        for obstacle in obstacles:
            yield obstacle
        return
    cell, grid, _ref = index
    inv = 1.0 / cell
    pad = max(0.0, float(halo))
    cx0 = int(math.floor((x - pad) * inv))
    cz0 = int(math.floor((z - pad) * inv))
    cx1 = int(math.floor((x + pad) * inv))
    cz1 = int(math.floor((z + pad) * inv))
    seen = set()
    for cx in range(cx0, cx1 + 1):
        for cz in range(cz0, cz1 + 1):
            bucket = grid.get((cx, cz))
            if not bucket:
                continue
            for obstacle in bucket:
                key = id(obstacle)
                if key in seen:
                    continue
                seen.add(key)
                yield obstacle


def iter_obstacles_near_segment(arena_type_id: int,
                                ax: float, az: float,
                                bx: float, bz: float,
                                halo: float = 0.0):
    obstacles = load_static_obstacles_for_arena(arena_type_id)
    if not obstacles:
        return
    index = get_static_obstacle_index(arena_type_id)
    if index is None:
        for obstacle in obstacles:
            yield obstacle
        return
    cell, grid, _ref = index
    inv = 1.0 / cell
    pad = max(0.0, float(halo))
    min_x = min(ax, bx) - pad
    max_x = max(ax, bx) + pad
    min_z = min(az, bz) - pad
    max_z = max(az, bz) + pad
    cx0 = int(math.floor(min_x * inv))
    cz0 = int(math.floor(min_z * inv))
    cx1 = int(math.floor(max_x * inv))
    cz1 = int(math.floor(max_z * inv))
    seen = set()
    for cx in range(cx0, cx1 + 1):
        for cz in range(cz0, cz1 + 1):
            bucket = grid.get((cx, cz))
            if not bucket:
                continue
            for obstacle in bucket:
                key = id(obstacle)
                if key in seen:
                    continue
                seen.add(key)
                yield obstacle


def prewarm_static_obstacles_for_enabled_maps():
    started = time.time()
    warmed = {}
    for arena_type_id in sorted(ENABLED_ARENA_TYPE_IDS):
        arena_started = time.time()
        if arena_type_id in STATIC_OBSTACLE_CACHE:
            obstacles = STATIC_OBSTACLE_CACHE[arena_type_id]
            arena_ms = (time.time() - arena_started) * 1000.0
            print(f"    [startup] static obstacles already warm "
                  f"arenaType={arena_type_id} count={len(obstacles)} "
                  f"in {arena_ms:.0f}ms")
        else:
            obstacles = load_static_obstacles_for_arena(arena_type_id)
            arena_ms = (time.time() - arena_started) * 1000.0
            print(f"    [startup] prewarmed static obstacles "
                  f"arenaType={arena_type_id} count={len(obstacles)} "
                  f"in {arena_ms:.0f}ms")
        warmed[arena_type_id] = len(obstacles)
    total_ms = (time.time() - started) * 1000.0
    print(f"    [startup] static obstacle prewarm total "
          f"maps={len(warmed)} in {total_ms:.0f}ms")
    return warmed


def find_blocking_static_obstacle(arena_type_id: int, x: float, z: float,
                                  tank_radius: float = STATIC_OBSTACLE_TANK_HALO):
    for obstacle in iter_obstacles_near_point(
            arena_type_id, x, z, halo=tank_radius):
        ox, oy, oz = obstacle[0], obstacle[1], obstacle[2]
        footprint = get_obstacle_move_footprint(obstacle)
        block_radius = get_obstacle_move_radius(obstacle) + tank_radius
        if block_radius <= 0.0:
            continue
        if footprint:
            if point_hits_footprint(x, z, footprint, tank_radius):
                return (ox, oy, oz, get_obstacle_move_radius(obstacle),
                        get_obstacle_shot_radius(obstacle), block_radius)
            continue
        dx = x - ox
        dz = z - oz
        if dx * dx + dz * dz <= block_radius * block_radius:
            return (ox, oy, oz, get_obstacle_move_radius(obstacle),
                    get_obstacle_shot_radius(obstacle), block_radius)
    return None


def find_blocking_static_obstacle_on_path(arena_type_id: int,
                                          prev_x: float, prev_z: float,
                                          new_x: float, new_z: float,
                                          tank_radius: float = STATIC_OBSTACLE_TANK_HALO):
    for obstacle in iter_obstacles_near_segment(
            arena_type_id, prev_x, prev_z, new_x, new_z, halo=tank_radius):
        ox, oy, oz = obstacle[0], obstacle[1], obstacle[2]
        footprint = get_obstacle_move_footprint(obstacle)
        block_radius = get_obstacle_move_radius(obstacle) + tank_radius
        if block_radius <= 0.0:
            continue
        if footprint:
            if segment_hits_footprint(prev_x, prev_z, new_x, new_z,
                                      footprint, tank_radius):
                return (ox, oy, oz, get_obstacle_move_radius(obstacle),
                        get_obstacle_shot_radius(obstacle), block_radius)
            continue
        if point_segment_distance_sq_xz(
                ox, oz, prev_x, prev_z, new_x, new_z) <= block_radius * block_radius:
            return (ox, oy, oz, get_obstacle_move_radius(obstacle),
                    get_obstacle_shot_radius(obstacle), block_radius)
    return None


def resolve_motion_against_obstacles(arena_type_id: int,
                                     prev_x: float, prev_z: float,
                                     new_x: float, new_z: float,
                                     tank_radius: float = STATIC_OBSTACLE_TANK_HALO):
    obstacle = find_blocking_static_obstacle_on_path(
        arena_type_id, prev_x, prev_z, new_x, new_z, tank_radius)
    if obstacle is None:
        return new_x, new_z, False
    if BATTLE_VERBOSE_DEBUG:
        print(f"    [motion] static obstacle block pos=({new_x:.1f},{new_z:.1f}) obstacle=({obstacle[0]:.1f},{obstacle[1]:.1f},{obstacle[2]:.1f}) moveR={obstacle[3]:.1f} blockR={obstacle[5]:.1f}")
    if (new_x != prev_x and find_blocking_static_obstacle_on_path(
            arena_type_id, prev_x, prev_z, new_x, prev_z, tank_radius) is None):
        return new_x, prev_z, True
    if (new_z != prev_z and find_blocking_static_obstacle_on_path(
            arena_type_id, prev_x, prev_z, prev_x, new_z, tank_radius) is None):
        return prev_x, new_z, True
    return prev_x, prev_z, True


def segment_point_distance_sq(prev_x: float, prev_z: float,
                              new_x: float, new_z: float,
                              point_x: float, point_z: float) -> float:
    dx = new_x - prev_x
    dz = new_z - prev_z
    length_sq = dx * dx + dz * dz
    if length_sq <= 0.000001:
        px = point_x - new_x
        pz = point_z - new_z
        return px * px + pz * pz
    t = ((point_x - prev_x) * dx + (point_z - prev_z) * dz) / length_sq
    t = max(0.0, min(1.0, t))
    closest_x = prev_x + dx * t
    closest_z = prev_z + dz * t
    px = point_x - closest_x
    pz = point_z - closest_z
    return px * px + pz * pz


def vehicle_blocks_motion(prev_x: float, prev_z: float,
                          new_x: float, new_z: float,
                          other_x: float, other_z: float,
                          block_radius: float) -> bool:
    radius_sq = block_radius * block_radius
    prev_dx = other_x - prev_x
    prev_dz = other_z - prev_z
    new_dx = other_x - new_x
    new_dz = other_z - new_z
    prev_sq = prev_dx * prev_dx + prev_dz * prev_dz
    new_sq = new_dx * new_dx + new_dz * new_dz
    if prev_sq < radius_sq:
        return new_sq <= prev_sq
    return segment_point_distance_sq(
        prev_x, prev_z, new_x, new_z, other_x, other_z) < radius_sq


def collision_normal_xz(prev_x: float, prev_z: float,
                        new_x: float, new_z: float,
                        other_x: float, other_z: float):
    nx = other_x - new_x
    nz = other_z - new_z
    length = math.sqrt(nx * nx + nz * nz)
    if length <= 0.001:
        nx = new_x - prev_x
        nz = new_z - prev_z
        length = math.sqrt(nx * nx + nz * nz)
    if length <= 0.001:
        return 0.0, 1.0
    return nx / length, nz / length


def find_blocking_vehicle_on_path(sess: dict, prev_x: float, prev_z: float,
                                  new_x: float, new_z: float,
                                  tank_radius: float = TANK_COLLISION_RADIUS,
                                  yaw: float = None,
                                  ignore_sess: dict = None):
    if not TANK_COLLISION_ENABLED:
        return None
    match_id = sess.get('battle_match_id')
    source_account_id = sess.get('account_id')
    source_vehicle = get_session_battle_vehicle(sess)
    source_half_width, source_half_length = vehicle_collision_dimensions(source_vehicle)
    min_radius = max(0.1, float(tank_radius))
    source_half_width = max(source_half_width, min_radius)
    source_half_length = max(source_half_length, min_radius)
    source_yaw = float(yaw if yaw is not None else sess.get('battle_yaw', 0.0))
    with battle_lock:
        candidates = list(active_battle_accounts.values())
    for other in candidates:
        if other is sess:
            continue
        if ignore_sess is not None and other is ignore_sess:
            continue
        if other.get('account_id') == source_account_id:
            continue
        if match_id is not None and other.get('battle_match_id') != match_id:
            continue
        if not other.get('battle_bundle_sent'):
            continue
        other_pos = get_effective_vehicle_pos(other, other.get('battle_pos'))
        if other_pos is None:
            continue
        other_x = float(other_pos[0])
        other_z = float(other_pos[2])
        other_vehicle = get_session_battle_vehicle(other)
        other_half_width, other_half_length = vehicle_collision_dimensions(other_vehicle)
        other_half_width = max(other_half_width, min_radius)
        other_half_length = max(other_half_length, min_radius)
        other_yaw = vehicle_hit_yaw(other)
        contact = vehicle_footprint_contact_on_path(
            prev_x, prev_z, new_x, new_z,
            source_yaw, source_half_width, source_half_length,
            other_x, other_z, other_yaw,
            other_half_width, other_half_length,
            TANK_COLLISION_MARGIN)
        if contact is not None:
            damage_contact = vehicle_footprint_contact_on_path(
                prev_x, prev_z, new_x, new_z,
                source_yaw, source_half_width, source_half_length,
                other_x, other_z, other_yaw,
                other_half_width, other_half_length,
                RAM_DAMAGE_CONTACT_MARGIN)
            resolved_contact = damage_contact if damage_contact is not None else contact
            block_radius = max(source_half_width, source_half_length) + max(
                other_half_width, other_half_length)
            normal_x, normal_z = collision_normal_xz(
                prev_x, prev_z, resolved_contact['x'], resolved_contact['z'],
                other_x, other_z)
            confirmed_contact = (
                damage_contact is not None and
                not bool(damage_contact.get('prev_overlap')))
            return {
                'session': other,
                'x': other_x,
                'z': other_z,
                'normal': (normal_x, normal_z),
                'block_radius': block_radius,
                'contact_pos': (resolved_contact['x'], resolved_contact['z']),
                'contact_t': float(resolved_contact.get('t', 0.0)),
                'contact_confirmed': confirmed_contact,
                'damage_contact_pos': (
                    (damage_contact['x'], damage_contact['z'])
                    if damage_contact is not None else None),
            }
    return None


def resolve_motion_against_vehicles(sess: dict,
                                    prev_x: float, prev_z: float,
                                    new_x: float, new_z: float,
                                    tank_radius: float = TANK_COLLISION_RADIUS,
                                    yaw: float = None,
                                    source_speed: float = None,
                                    source_rspeed: float = None,
                                    ignore_sess: dict = None):
    blocker = find_blocking_vehicle_on_path(
        sess, prev_x, prev_z, new_x, new_z, tank_radius, yaw=yaw,
        ignore_sess=ignore_sess)
    if blocker is None:
        sess['battle_motion_blocked_by_vehicle'] = False
        sess.pop('battle_vehicle_collision_info', None)
        return new_x, new_z, False
    sess['battle_motion_blocked_by_vehicle'] = True
    sess['battle_motion_force_position'] = True
    sess['battle_last_vehicle_collision_time'] = time.time()
    info = dict(blocker)
    info['prev'] = (prev_x, prev_z)
    info['attempt'] = (new_x, new_z)
    info['source_yaw'] = float(yaw if yaw is not None else sess.get('battle_yaw', 0.0))
    info['source_speed'] = float(source_speed if source_speed is not None else sess.get('battle_speed', 0.0))
    info['source_rspeed'] = float(source_rspeed if source_rspeed is not None else sess.get('battle_rspeed', 0.0))
    sess['battle_vehicle_collision_info'] = info
    if BATTLE_VERBOSE_DEBUG:
        other = blocker['session']
        other_x = blocker['x']
        other_z = blocker['z']
        block_radius = blocker['block_radius']
        print(f"    [motion] vehicle block pos=({new_x:.1f},{new_z:.1f}) "
              f"other={other.get('username') or other.get('account_id')} "
              f"otherPos=({other_x:.1f},{other_z:.1f}) blockR={block_radius:.1f}")
    if (new_x != prev_x and find_blocking_vehicle_on_path(
            sess, prev_x, prev_z, new_x, prev_z, tank_radius, yaw=yaw,
            ignore_sess=ignore_sess) is None):
        return new_x, prev_z, True
    if (new_z != prev_z and find_blocking_vehicle_on_path(
            sess, prev_x, prev_z, prev_x, new_z, tank_radius, yaw=yaw,
            ignore_sess=ignore_sess) is None):
        return prev_x, new_z, True
    contact_pos = blocker.get('contact_pos') or (prev_x, prev_z)
    return float(contact_pos[0]), float(contact_pos[1]), True


def ray_static_obstacle_hit(source_sess: dict, shot_pos, shot_vec, hit_distance,
                            shooter_gap: float = STATIC_OBSTACLE_SHOOTER_GAP,
                            target_gap: float = STATIC_OBSTACLE_TARGET_GAP):
    horizontal = shot_vec[0] * shot_vec[0] + shot_vec[2] * shot_vec[2]
    if horizontal <= 0.000001:
        return None
    arena_type_id = int(source_sess.get('battle_arena_type_id') or ARENA_TYPE_KARELIA)
    end_x = shot_pos[0] + shot_vec[0] * hit_distance
    end_y = shot_pos[1] + shot_vec[1] * hit_distance
    end_z = shot_pos[2] + shot_vec[2] * hit_distance
    best = None
    ray_halo = max(STATIC_OBSTACLE_SHOOTER_GAP, STATIC_OBSTACLE_TARGET_GAP)
    for obstacle in iter_obstacles_near_segment(
            arena_type_id, shot_pos[0], shot_pos[2], end_x, end_z,
            halo=ray_halo):
        x, y, z = obstacle[0], obstacle[1], obstacle[2]
        radius = get_obstacle_shot_radius(obstacle)
        dx_s = x - shot_pos[0]
        dy_s = y - shot_pos[1]
        dz_s = z - shot_pos[2]
        if dx_s * dx_s + dy_s * dy_s + dz_s * dz_s < (radius + shooter_gap) ** 2:
            continue
        dx_t = x - end_x
        dy_t = y - end_y
        dz_t = z - end_z
        # Only skip obstacles that sit AT or PAST the target along the ray
        # (the "tank parked against a wall behind it" case).  Obstacles in
        # FRONT of the target along the ray must remain candidates, otherwise
        # a stone right in front of a target tank would never block the shot.
        along_target_offset = (
            dx_t * shot_vec[0] + dz_t * shot_vec[2]) / horizontal
        if along_target_offset >= -STATIC_OBSTACLE_SHOT_RADIUS_PAD and \
                dx_t * dx_t + dy_t * dy_t + dz_t * dz_t < (radius + target_gap) ** 2:
            continue
        dx = x - shot_pos[0]
        dz = z - shot_pos[2]
        distance = (dx * shot_vec[0] + dz * shot_vec[2]) / horizontal
        if distance <= 0.0 or distance >= hit_distance:
            continue
        closest_x = shot_pos[0] + shot_vec[0] * distance
        closest_z = shot_pos[2] + shot_vec[2] * distance
        miss_x = x - closest_x
        miss_z = z - closest_z
        if miss_x * miss_x + miss_z * miss_z > radius * radius:
            continue
        miss_sq = miss_x * miss_x + miss_z * miss_z
        entry_distance = distance - math.sqrt(max(0.0, radius * radius - miss_sq))
        exit_distance = distance + math.sqrt(max(0.0, radius * radius - miss_sq))
        if exit_distance <= 0.0 or entry_distance >= hit_distance:
            continue
        if entry_distance <= 0.0:
            if shooter_gap > 0.0:
                continue
            entry_distance = 0.0
        hit_x = shot_pos[0] + shot_vec[0] * entry_distance
        hit_y = shot_pos[1] + shot_vec[1] * entry_distance
        hit_z = shot_pos[2] + shot_vec[2] * entry_distance
        if hit_y < y - STATIC_OBSTACLE_Y_BELOW or hit_y > y + STATIC_OBSTACLE_Y_HEIGHT:
            continue
        score = (entry_distance, hit_x, hit_y, hit_z, radius)
        if best is None or score[0] < best[0]:
            best = score
    return best


def marker_static_obstacle_hit(source_sess: dict, shot_pos, marker_pos):
    dx = float(marker_pos[0]) - float(shot_pos[0])
    dy = float(marker_pos[1]) - float(shot_pos[1])
    dz = float(marker_pos[2]) - float(shot_pos[2])
    distance = math.sqrt(dx * dx + dy * dy + dz * dz)
    if distance <= 0.001:
        return None
    marker_vec = (dx / distance, dy / distance, dz / distance)
    return ray_static_obstacle_hit(source_sess, shot_pos, marker_vec, distance,
                                   target_gap=0.0)


def static_obstacle_blocks_artillery_splash(source_sess: dict, marker_pos,
                                            target_pos):
    """Check whether a static obstacle stands between an artillery explosion
    point (marker_pos) and a tank center (target_pos).

    Used to prevent artillery splash damage from passing through stones: if a
    rock is between the impact point and the tank, the splash should be
    absorbed by the rock and not reach the tank.
    """
    if not ARTILLERY_OBSTACLE_BLOCKS_SHOT:
        return False
    mx = float(marker_pos[0])
    my = float(marker_pos[1])
    mz = float(marker_pos[2])
    tx = float(target_pos[0])
    ty = float(target_pos[1]) + SHOT_TANK_CENTER_HEIGHT
    tz = float(target_pos[2])
    dx = tx - mx
    dy = ty - my
    dz = tz - mz
    distance = math.sqrt(dx * dx + dy * dy + dz * dz)
    if distance <= 0.001:
        return False
    direction = (dx / distance, dy / distance, dz / distance)
    obstacle = ray_static_obstacle_hit(
        source_sess, (mx, my, mz), direction, distance,
        shooter_gap=0.0, target_gap=ARTILLERY_SPLASH_OBSTACLE_TARGET_GAP)
    return obstacle is not None


def artillery_descent_blocked_by_obstacle(source_sess: dict, marker_pos,
                                          shot_pos):
    """Check whether the visible artillery shell descent path is blocked by a
    static obstacle.

    The visible shell is built so that it begins
    `ARTILLERY_VISIBLE_TRACER_BACK_DISTANCE` metres horizontally back from the
    marker and `ARTILLERY_VISIBLE_TRACER_HEIGHT` metres above it, descending
    along the vector from the player toward the marker.  If a tall stone sits
    between this descent start and the marker, the shell visually crashes into
    the stone before reaching the ground.
    """
    if not ARTILLERY_OBSTACLE_BLOCKS_SHOT:
        return False
    mx = float(marker_pos[0])
    my = float(marker_pos[1])
    mz = float(marker_pos[2])
    sx = float(shot_pos[0])
    sz = float(shot_pos[2])
    horizontal_dx = mx - sx
    horizontal_dz = mz - sz
    horizontal = math.sqrt(horizontal_dx * horizontal_dx +
                           horizontal_dz * horizontal_dz)
    if horizontal <= 0.001:
        return False
    dir_x = horizontal_dx / horizontal
    dir_z = horizontal_dz / horizontal
    descent_start = (
        mx - dir_x * ARTILLERY_VISIBLE_TRACER_BACK_DISTANCE,
        my + ARTILLERY_VISIBLE_TRACER_HEIGHT,
        mz - dir_z * ARTILLERY_VISIBLE_TRACER_BACK_DISTANCE,
    )
    desc_dx = mx - descent_start[0]
    desc_dy = my - descent_start[1]
    desc_dz = mz - descent_start[2]
    descent_distance = math.sqrt(desc_dx * desc_dx + desc_dy * desc_dy +
                                 desc_dz * desc_dz)
    if descent_distance <= 0.001:
        return False
    descent_dir = (desc_dx / descent_distance,
                   desc_dy / descent_distance,
                   desc_dz / descent_distance)
    obstacle = ray_static_obstacle_hit(
        source_sess, descent_start, descent_dir, descent_distance,
        shooter_gap=0.0,
        target_gap=ARTILLERY_DESCENT_OBSTACLE_TARGET_GAP)
    return obstacle is not None


def aim_point_miss(target_pos, center):
    if target_pos is None:
        return None
    dx = center[0] - float(target_pos[0])
    dz = center[2] - float(target_pos[2])
    horizontal = math.sqrt(dx * dx + dz * dz)
    vertical = abs(center[1] - float(target_pos[1]))
    if horizontal <= TARGET_AIM_RADIUS and vertical <= TARGET_AIM_RADIUS:
        return horizontal + vertical * 0.25
    return None


def is_target_marker_fresh(source_sess: dict, marker_time=None) -> bool:
    last = safe_float(source_sess.get('battle_target_pos_time', 0.0)
                      if marker_time is None else marker_time, 0.0)
    return last > 0.0 and time.time() - last <= TARGET_MARKER_OCCLUSION_MAX_AGE


def marker_blocks_shot(source_sess: dict, target_ray_distance, hit_distance,
                       marker_time=None, ray_miss=None, aim_miss=None) -> bool:
    if target_ray_distance is None:
        return False
    if not is_target_marker_fresh(source_sess, marker_time):
        return False
    if aim_miss is not None:
        return False
    if ray_miss is not None and ray_miss <= TARGET_HIT_RADIUS:
        return False
    return hit_distance > target_ray_distance + SHOT_TARGET_OVERSHOOT


def get_artillery_explosion_radius(shell: dict) -> float:
    radius = safe_float((shell or {}).get('explosionRadius'), 0.0, 0.0)
    if is_high_explosive_shell(shell):
        caliber_radius = get_shell_caliber(shell) / ARTILLERY_SPLASH_CALIBER_FACTOR
        radius = max(radius * ARTILLERY_SPLASH_RADIUS_FACTOR,
                     caliber_radius)
    return min(radius, ARTILLERY_SPLASH_MAX_RADIUS)


def artillery_splash_damage(shell: dict, splash_distance: float,
                            radius: float) -> int:
    if radius <= 0.0:
        return 0
    base = get_shell_damage(shell)
    distance_factor = clamp(1.0 - splash_distance / radius, 0.0, 1.0)
    if distance_factor <= 0.0:
        return 0
    factor = ARTILLERY_SPLASH_MIN_FACTOR + (
        1.0 - ARTILLERY_SPLASH_MIN_FACTOR) * distance_factor
    return max(SHOT_HE_MIN_SPLASH_DAMAGE, int(round(base * factor)))


def artillery_direct_he_damage(shell: dict) -> int:
    base = get_shell_damage(shell)
    return max(SHOT_HE_MIN_SPLASH_DAMAGE,
               int(round(base * ARTILLERY_DIRECT_HE_DAMAGE_FACTOR)))


def make_artillery_hit_info(source_sess: dict, target: dict, target_pos,
                            marker_pos, shot_pos, shot_vec, direct_hit: bool,
                            splash_distance: float = 0.0,
                            splash_radius: float = 0.0):
    vehicle = get_session_battle_vehicle(target)
    armor_model = vehicle_armor_model(vehicle)
    dims = armor_dimensions(armor_model)
    yaw = vehicle_hit_yaw(target)
    fallback_hit = fallback_hit_from_marker(marker_pos, target_pos, yaw, dims)
    if fallback_hit is None:
        return None
    hit_local, hit_world, normal = fallback_hit
    if direct_hit:
        component, zone = armor_component_and_zone(hit_local, normal, dims)
        armor = get_zone_armor(armor_model, component, zone)
        armor *= get_component_homogenization(armor_model, component)
    else:
        component, zone, armor = 'hull', 'splash', 0.0
        hit_world = tuple(float(v) for v in marker_pos)
        normal = (0.0, 1.0, 0.0)
    hit_distance = math.sqrt(
        (hit_world[0] - shot_pos[0]) ** 2 +
        (hit_world[1] - shot_pos[1]) ** 2 +
        (hit_world[2] - shot_pos[2]) ** 2)
    local_end = world_to_vehicle_local(
        (shot_pos[0] + shot_vec[0], shot_pos[1] + shot_vec[1], shot_pos[2] + shot_vec[2]),
        target_pos, yaw)
    local_origin = world_to_vehicle_local(shot_pos, target_pos, yaw)
    local_shot_dir = normalize_vec((
        local_end[0] - local_origin[0],
        local_end[1] - local_origin[1],
        local_end[2] - local_origin[2],
    ))
    return {
        'target': target,
        'targetPos': target_pos,
        'hitWorld': hit_world,
        'hitLocal': hit_local,
        'normal': normal,
        'distance': hit_distance,
        'component': component,
        'zone': zone,
        'armor': armor,
        'impactCos': 1.0,
        'dimensions': dims,
        'localShotDir': local_shot_dir,
        'artilleryDirectHit': direct_hit,
        'artillerySplash': not direct_hit,
        'splashDistance': splash_distance,
        'splashRadius': splash_radius,
    }


def find_artillery_shot_target(source_sess: dict, shot_pos, shot_vec):
    marker_pos = safe_vec3(source_sess.get('battle_last_shot_target_pos'), None)
    marker_time = source_sess.get('battle_last_shot_target_pos_time',
                                  source_sess.get('battle_target_pos_time', 0.0))
    if marker_pos is None or not is_target_marker_fresh(source_sess, marker_time):
        print("    [shot] miss: no fresh artillery aim marker")
        return None
    shell = source_sess.get('battle_last_shot_shell') or get_current_vehicle_shell(source_sess)
    radius = get_artillery_explosion_radius(shell)
    source_account_id = source_sess.get('account_id')
    source_team = source_sess.get('battle_team')
    match_id = source_sess.get('battle_match_id')
    best_direct = None
    best_splash = None
    with battle_lock:
        candidates = list(active_battle_accounts.values())
    for target in candidates:
        if target.get('account_id') == source_account_id:
            continue
        if target.get('battle_match_id') != match_id:
            continue
        if source_team is not None and target.get('battle_team') == source_team:
            continue
        if int(target.get('battle_vehicle_health') or 0) <= 0:
            continue
        for pos in unique_positions((
                target.get('battle_pos'),
                target.get('battle_prev_pos'),
                target.get('client_vehicle_pos'),
        )):
            center = (pos[0], pos[1] + SHOT_TANK_CENTER_HEIGHT, pos[2])
            dx = center[0] - marker_pos[0]
            dy = center[1] - marker_pos[1]
            dz = center[2] - marker_pos[2]
            horizontal = math.sqrt(dx * dx + dz * dz)
            vertical = abs(dy)
            in_direct = (horizontal <= ARTILLERY_DIRECT_HIT_RADIUS_H and
                         vertical <= ARTILLERY_DIRECT_HIT_RADIUS_V)
            ground_distance = math.sqrt(
                (pos[0] - marker_pos[0]) ** 2 +
                (pos[2] - marker_pos[2]) ** 2)
            in_splash = (radius > 0.0 and ground_distance <= radius)
            if not in_direct and not in_splash:
                continue
            if static_obstacle_blocks_artillery_splash(
                    source_sess, marker_pos, pos):
                print(f"    [shot] artillery splash to "
                      f"{target.get('username') or 'bot'} "
                      f"blocked by static obstacle dist={ground_distance:.2f}m")
                continue
            if in_direct:
                score = horizontal + vertical * 0.1
                if best_direct is None or score < best_direct[0]:
                    best_direct = (score, target, pos)
            if in_splash:
                if best_splash is None or ground_distance < best_splash[0]:
                    best_splash = (ground_distance, target, pos)
    if best_direct is not None:
        _score, target, pos = best_direct
        print(f"    [shot] artillery direct hit marker_h={_score:.2f}m")
        return make_artillery_hit_info(source_sess, target, pos, marker_pos,
                                       shot_pos, shot_vec, True)
    if best_splash is not None:
        distance, target, pos = best_splash
        print(f"    [shot] artillery splash dist={distance:.2f}m radius={radius:.2f}m")
        return make_artillery_hit_info(source_sess, target, pos, marker_pos,
                                       shot_pos, shot_vec, False,
                                       distance, radius)
    print(f"    [shot] miss: no tank in artillery splash marker_pos={marker_pos} radius={radius:.2f}")
    return None


def find_direct_ray_shot_candidate(source_sess: dict, shot_pos, shot_vec,
                                   candidates):
    source_account_id = source_sess.get('account_id')
    source_team = source_sess.get('battle_team')
    match_id = source_sess.get('battle_match_id')
    best = None
    for target in candidates:
        if target.get('account_id') == source_account_id:
            continue
        if target.get('battle_match_id') != match_id:
            continue
        if source_team is not None and target.get('battle_team') == source_team:
            continue
        if int(target.get('battle_vehicle_health') or 0) <= 0:
            continue
        vehicle = get_session_battle_vehicle(target)
        armor_model = vehicle_armor_model(vehicle)
        dims = armor_dimensions(armor_model)
        yaw = vehicle_hit_yaw(target)
        positions = unique_positions((
            target.get('battle_pos'),
            target.get('battle_prev_pos'),
            target.get('client_vehicle_pos'),
        ))
        for pos in positions:
            ray_hit = ray_vehicle_box_hit(shot_pos, shot_vec, pos, yaw, dims)
            if ray_hit is None:
                continue
            distance = float(ray_hit[3])
            if best is None or distance < best['distance']:
                best = {
                    'target': target,
                    'pos': pos,
                    'center': (pos[0], pos[1] + SHOT_TANK_CENTER_HEIGHT, pos[2]),
                    'vehicle': vehicle,
                    'armorModel': armor_model,
                    'dims': dims,
                    'yaw': yaw,
                    'rayHit': ray_hit,
                    'distance': distance,
                }
    return best


def find_shot_target(source_sess: dict, shot_pos, shot_vec):
    shot_pos = safe_vec3(shot_pos, None)
    shot_vec = safe_vec3(shot_vec, None)
    if shot_pos is None or shot_vec is None:
        print("    [shot] miss: invalid shot vector")
        return None
    shot_vec = normalize_vec(shot_vec)
    if is_artillery_session(source_sess):
        return find_artillery_shot_target(source_sess, shot_pos, shot_vec)
    source_account_id = source_sess.get('account_id')
    source_team = source_sess.get('battle_team')
    match_id = source_sess.get('battle_match_id')

    marker_pos = source_sess.get('battle_last_shot_target_pos')
    marker_time = source_sess.get('battle_last_shot_target_pos_time',
                                  source_sess.get('battle_target_pos_time', 0.0))
    marker_pos = safe_vec3(marker_pos, None)
    marker_fresh = (marker_pos is not None and
                    is_target_marker_fresh(source_sess, marker_time))

    with battle_lock:
        candidates = list(active_battle_accounts.values())

    best_target = None
    best_h = None
    best_center = None
    best_pos = None
    if marker_fresh:
        for target in candidates:
            if target.get('account_id') == source_account_id:
                continue
            if target.get('battle_match_id') != match_id:
                continue
            if source_team is not None and target.get('battle_team') == source_team:
                continue
            health = int(target.get('battle_vehicle_health') or 0)
            if health <= 0:
                continue
            positions = unique_positions((
                target.get('battle_pos'),
                target.get('battle_prev_pos'),
                target.get('client_vehicle_pos'),
            ))
            if not positions:
                continue
            for pos in positions:
                center = (pos[0], pos[1] + SHOT_TANK_CENTER_HEIGHT, pos[2])
                dx = center[0] - float(marker_pos[0])
                dz = center[2] - float(marker_pos[2])
                horizontal = math.sqrt(dx * dx + dz * dz)
                vertical_signed = center[1] - float(marker_pos[1])
                if horizontal > SHOT_TANK_HIT_RADIUS_H:
                    continue
                if vertical_signed < -SHOT_TANK_HIT_RADIUS_V:
                    continue
                if vertical_signed > SHOT_TANK_MARKER_VERT_ABOVE:
                    continue
                if best_target is None or horizontal < best_h:
                    best_target = target
                    best_h = horizontal
                    best_center = center
                    best_pos = pos

    ray_candidate = find_direct_ray_shot_candidate(
        source_sess, shot_pos, shot_vec, candidates)
    chosen_ray_candidate = None
    if ray_candidate is not None:
        use_ray_candidate = best_target is None
        if best_target is not None:
            vehicle = get_session_battle_vehicle(best_target)
            armor_model = vehicle_armor_model(vehicle)
            dims = armor_dimensions(armor_model)
            yaw = vehicle_hit_yaw(best_target)
            marker_ray_hit = ray_vehicle_box_hit(
                shot_pos, shot_vec, best_pos, yaw, dims)
            marker_distance = (
                float(marker_ray_hit[3]) if marker_ray_hit is not None
                else None)
            if (marker_distance is None or
                    ray_candidate['distance'] + 0.001 < marker_distance):
                use_ray_candidate = True
        if use_ray_candidate:
            chosen_ray_candidate = ray_candidate
            best_target = ray_candidate['target']
            best_pos = ray_candidate['pos']
            best_center = ray_candidate['center']
            best_h = None

    if best_target is None:
        if not marker_fresh:
            print("    [shot] miss: no fresh client aim marker and no direct ray hit")
            return None
        print(f"    [shot] miss: no tank near client aim marker")
        print(f"    [shot] debug: marker_pos={marker_pos} marker_time={marker_time:.3f} age={time.time()-marker_time:.3f}")
        print(f"    [shot] debug: candidates={len(candidates)} match_id={match_id} source_team={source_team}")
        for target in candidates:
            if target.get('account_id') == source_account_id:
                continue
            if target.get('battle_match_id') != match_id:
                continue
            if source_team is not None and target.get('battle_team') == source_team:
                continue
            health = int(target.get('battle_vehicle_health') or 0)
            if health <= 0:
                continue
            positions = unique_positions((
                target.get('battle_pos'),
                target.get('battle_prev_pos'),
                target.get('client_vehicle_pos'),
            ))
            uname = target.get('username') or 'bot'
            for pos in positions:
                center = (pos[0], pos[1] + SHOT_TANK_CENTER_HEIGHT, pos[2])
                dx = center[0] - float(marker_pos[0])
                dz = center[2] - float(marker_pos[2])
                horizontal = math.sqrt(dx * dx + dz * dz)
                vertical_signed = center[1] - float(marker_pos[1])
                print(f"    [shot] debug: {uname} pos={pos} center={center} h={horizontal:.2f} v_signed={vertical_signed:+.2f} (limits H={SHOT_TANK_HIT_RADIUS_H} V_below={SHOT_TANK_HIT_RADIUS_V} V_above={SHOT_TANK_MARKER_VERT_ABOVE})")
        return None

    if chosen_ray_candidate is not None:
        vehicle = chosen_ray_candidate['vehicle']
        armor_model = chosen_ray_candidate['armorModel']
        dims = chosen_ray_candidate['dims']
        yaw = chosen_ray_candidate['yaw']
        ray_hit = chosen_ray_candidate['rayHit']
    else:
        vehicle = get_session_battle_vehicle(best_target)
        armor_model = vehicle_armor_model(vehicle)
        dims = armor_dimensions(armor_model)
        yaw = vehicle_hit_yaw(best_target)
        ray_hit = ray_vehicle_box_hit(shot_pos, shot_vec, best_pos, yaw, dims)
    if ray_hit is None:
        fallback_hit = fallback_hit_from_marker(marker_pos, best_pos, yaw, dims)
        if fallback_hit is None:
            print("    [shot] miss: invalid fallback hit")
            return None
        hit_local, hit_world, normal = fallback_hit
        dx = hit_world[0] - shot_pos[0]
        dy = hit_world[1] - shot_pos[1]
        dz = hit_world[2] - shot_pos[2]
        hit_distance = max(0.001, math.sqrt(dx * dx + dy * dy + dz * dz))
    else:
        hit_local, hit_world, normal, hit_distance = ray_hit
    if hit_distance > 0.001:
        target_vec = (
            (hit_world[0] - shot_pos[0]) / hit_distance,
            (hit_world[1] - shot_pos[1]) / hit_distance,
            (hit_world[2] - shot_pos[2]) / hit_distance,
        )
        obstacle = ray_static_obstacle_hit(source_sess, shot_pos, target_vec, hit_distance)
        if obstacle is not None:
            obstacle_distance, obstacle_x, obstacle_y, obstacle_z, obstacle_radius = obstacle
            ox = float(obstacle_x); oz = float(obstacle_z)
            closest_x = shot_pos[0] + target_vec[0] * obstacle_distance
            closest_z = shot_pos[2] + target_vec[2] * obstacle_distance
            ray_y_at = shot_pos[1] + target_vec[1] * obstacle_distance
            miss_xz = math.sqrt((ox - closest_x) ** 2 + (oz - closest_z) ** 2)
            print(f"    [shot] miss: static obstacle blocks line of fire dist={obstacle_distance:.1f}/{hit_distance:.1f} obstacle=({obstacle_x:.1f},{obstacle_y:.1f},{obstacle_z:.1f}) r={obstacle_radius:.1f}")
            print(f"    [shot] debug: shot_pos={shot_pos} target_vec=({target_vec[0]:.3f},{target_vec[1]:.3f},{target_vec[2]:.3f}) best_center={best_center}")
            print(f"    [shot] debug: ray_at_obstacle=({closest_x:.1f},{ray_y_at:.1f},{closest_z:.1f}) miss_xz={miss_xz:.2f} y_band=[{obstacle_y - STATIC_OBSTACLE_Y_BELOW:.1f},{obstacle_y + STATIC_OBSTACLE_Y_HEIGHT:.1f}]")
            return None
    local_end = world_to_vehicle_local(
        (shot_pos[0] + shot_vec[0], shot_pos[1] + shot_vec[1], shot_pos[2] + shot_vec[2]),
        best_pos, yaw)
    local_origin = world_to_vehicle_local(shot_pos, best_pos, yaw)
    local_shot_dir = normalize_vec((
        local_end[0] - local_origin[0],
        local_end[1] - local_origin[1],
        local_end[2] - local_origin[2],
    ))
    marker_hit_local = marker_inside_vehicle_box(marker_pos, best_pos, yaw, dims)
    if marker_hit_local is not None:
        current_cos = impact_cosine(shot_vec, normal, yaw)
        marker_normal = direct_marker_entry_normal(local_shot_dir)
        marker_cos = impact_cosine(shot_vec, marker_normal, yaw)
        if current_cos <= SHOT_ARMOR_AUTORICOCHET_COS and marker_cos > current_cos:
            hit_local = marker_hit_local
            hit_world = tuple(float(v) for v in marker_pos)
            dx = hit_world[0] - shot_pos[0]
            dy = hit_world[1] - shot_pos[1]
            dz = hit_world[2] - shot_pos[2]
            hit_distance = max(0.001, math.sqrt(dx * dx + dy * dy + dz * dz))
            normal = marker_normal
    component, zone = armor_component_and_zone(hit_local, normal, dims)
    armor = get_zone_armor(armor_model, component, zone)
    armor *= get_component_homogenization(armor_model, component)
    if best_h is None:
        print(f"    [shot] hit: ray_dist={hit_distance:.2f}m component={component} zone={zone}")
    else:
        print(f"    [shot] hit: marker_h={best_h:.2f}m component={component} zone={zone}")
    return {
        'target': best_target,
        'targetPos': best_pos,
        'hitWorld': hit_world,
        'hitLocal': hit_local,
        'normal': normal,
        'distance': hit_distance,
        'component': component,
        'zone': zone,
        'armor': armor,
        'impactCos': impact_cosine(shot_vec, normal, yaw),
        'dimensions': dims,
        'localShotDir': local_shot_dir,
    }


def build_health_update_for_viewer(viewer_sess: dict, target_sess: dict) -> bytes:
    health = int(target_sess.get('battle_vehicle_health') or 0)
    is_crew_active = health > 0
    if viewer_sess is target_sess:
        return (
            build_avatar_update_vehicle_health(health, is_crew_active) +
            build_vehicle_health_property_update(PLAYER_VEHICLE_ID, health,
                                                 is_crew_active)
        )
    return build_vehicle_health_property_update(get_remote_vehicle_id(target_sess),
                                                health, is_crew_active)


def viewer_has_known_remote_vehicle(viewer_sess: dict,
                                    target_sess: dict) -> bool:
    account_id = target_sess.get('account_id')
    return account_id is not None and account_id in viewer_sess.get(
        'known_remote_accounts', set())


def force_shot_feedback_to_source(viewer_sess: dict, source_sess: dict,
                                  target_sess: dict) -> bool:
    return (
        viewer_sess is source_sess and
        viewer_sess is not target_sess and
        viewer_has_known_remote_vehicle(viewer_sess, target_sess))


def broadcast_vehicle_health(sock, target_sess: dict, source_sess: dict,
                             damage: int):
    match_id = target_sess.get('battle_match_id')
    target_account_id = target_sess.get('account_id')
    with battle_lock:
        viewers = [sess for sess in active_battle_accounts.values()
                   if sess.get('battle_match_id') == match_id]
    for viewer in viewers:
        addr = viewer.get('addr')
        if not addr:
            continue
        if viewer is not target_sess:
            known = viewer.setdefault('known_remote_accounts', set())
            force_feedback = force_shot_feedback_to_source(
                viewer, source_sess, target_sess)
            if not force_feedback:
                if not update_remote_vehicle_visibility(sock, viewer, target_sess):
                    continue
                if target_account_id not in known:
                    send_remote_vehicle(sock, viewer, target_sess)
        send_avatar_messages(sock, addr, viewer,
                             build_health_update_for_viewer(viewer, target_sess),
                             '',
                             reliable=True)
    print(f"    [damage] {source_sess.get('username') or 'player'} -> "
          f"{target_sess.get('username') or 'player'} -{damage} hp "
          f"({max(0, int(target_sess.get('battle_vehicle_health') or 0))} left)")


def vehicle_velocity_xz(sess: dict, speed=None, yaw=None):
    if speed is None:
        if (not sess.get('server_vehicle_authoritative', True) and
                is_recent_client_vehicle_position(sess)):
            speed = sess.get('client_vehicle_observed_speed', 0.0)
        else:
            speed = sess.get('battle_speed', 0.0)
    if yaw is None:
        yaw = vehicle_hit_yaw(sess)
    speed = float(speed or 0.0)
    yaw = float(yaw or 0.0)
    return math.sin(yaw) * speed, math.cos(yaw) * speed


def ram_closing_speed(source_sess: dict, target_sess: dict,
                      collision_info: dict) -> float:
    normal_x, normal_z = collision_info.get('normal') or (0.0, 1.0)
    source_vx, source_vz = vehicle_velocity_xz(
        source_sess,
        collision_info.get('source_speed'),
        collision_info.get('source_yaw'))
    target_vx, target_vz = vehicle_velocity_xz(target_sess)
    closing = (source_vx - target_vx) * normal_x + (source_vz - target_vz) * normal_z
    return max(0.0, float(closing))


def ram_damage_contact_confirmed(source_sess: dict, target_sess: dict,
                                 collision_info: dict) -> bool:
    if not collision_info.get('contact_confirmed'):
        return False
    contact_pos = collision_info.get('damage_contact_pos') or collision_info.get('contact_pos')
    if not contact_pos:
        return False
    target_pos = get_effective_vehicle_pos(target_sess, target_sess.get('battle_pos'))
    if target_pos is None:
        return False
    source_vehicle = get_session_battle_vehicle(source_sess)
    target_vehicle = get_session_battle_vehicle(target_sess)
    source_half_width, source_half_length = vehicle_collision_dimensions(source_vehicle)
    target_half_width, target_half_length = vehicle_collision_dimensions(target_vehicle)
    source_yaw = float(collision_info.get('source_yaw', source_sess.get('battle_yaw', 0.0)))
    return vehicle_footprints_overlap(
        float(contact_pos[0]), float(contact_pos[1]), source_yaw,
        source_half_width, source_half_length,
        float(target_pos[0]), float(target_pos[2]), vehicle_hit_yaw(target_sess),
        target_half_width, target_half_length,
        RAM_DAMAGE_CONTACT_MARGIN)


def ram_pair_key(source_sess: dict, target_sess: dict):
    source_id = source_sess.get('account_id') or id(source_sess)
    target_id = target_sess.get('account_id') or id(target_sess)
    source_id = int(source_id)
    target_id = int(target_id)
    return (source_id, target_id) if source_id <= target_id else (target_id, source_id)


def claim_ram_cooldown(source_sess: dict, target_sess: dict,
                       now: float) -> bool:
    key = ram_pair_key(source_sess, target_sess)
    source_times = source_sess.setdefault('battle_ram_contact_times', {})
    target_times = target_sess.setdefault('battle_ram_contact_times', {})
    last = max(float(source_times.get(key, -999999.0)),
               float(target_times.get(key, -999999.0)))
    if now - last < RAM_DAMAGE_COOLDOWN_SECONDS:
        return False
    source_times[key] = now
    target_times[key] = now
    return True


def send_forced_vehicle_position(sock, sess: dict):
    addr = sess.get('addr')
    if not addr:
        return False
    pos = get_effective_vehicle_pos(
        sess, sess.get('battle_pos', ARENA_SPAWN_POS[ARENA_TYPE_KARELIA]))
    yaw = float(sess.get('battle_yaw', 0.0))
    return send_avatar_messages(
        sock, addr, sess,
        build_forced_position(PLAYER_VEHICLE_ID, pos, yaw,
                              space_id=SPACE_ID, vehicle_id=0),
        '',
        reliable=False)


def try_push_ram_target(source_sess: dict, target_sess: dict,
                        normal_x: float, normal_z: float) -> bool:
    if RAM_PUSH_DISTANCE <= 0.0:
        return False
    pos = get_effective_vehicle_pos(target_sess, target_sess.get('battle_pos'))
    if pos is None:
        return False
    arena_type_id = normalize_arena_type_id(
        target_sess.get('battle_arena_type_id') or
        source_sess.get('battle_arena_type_id'))
    x, y, z = (float(pos[0]), float(pos[1]), float(pos[2]))
    new_x = x + normal_x * RAM_PUSH_DISTANCE
    new_z = z + normal_z * RAM_PUSH_DISTANCE
    new_x, new_z, blocked = resolve_motion_against_obstacles(
        arena_type_id, x, z, new_x, new_z)
    if blocked:
        return False
    if find_blocking_vehicle_on_path(
            target_sess, x, z, new_x, new_z,
            yaw=vehicle_hit_yaw(target_sess),
            ignore_sess=source_sess) is not None:
        return False
    new_y, _normal = sample_terrain(arena_type_id, new_x, new_z, y)
    target_sess['battle_prev_pos'] = (x, y, z)
    target_sess['battle_pos'] = (new_x, new_y, new_z)
    target_sess['battle_speed'] = 0.0
    target_sess['battle_rspeed'] = 0.0
    target_sess['battle_motion_force_position'] = True
    return True


def sync_ram_collision(sock, source_sess: dict, target_sess: dict,
                       normal_x: float, normal_z: float,
                       push_target: bool = False):
    source_sess['battle_speed'] = 0.0
    source_sess['battle_rspeed'] = 0.0
    if push_target:
        try_push_ram_target(source_sess, target_sess, normal_x, normal_z)
    source_sess['battle_motion_force_position'] = True
    target_sess['battle_motion_force_position'] = True
    send_forced_vehicle_position(sock, source_sess)
    send_forced_vehicle_position(sock, target_sess)


def apply_contact_damage(sock, source_sess: dict, target_sess: dict,
                         damage: int) -> int:
    health = int(target_sess.get('battle_vehicle_health') or 0)
    if health <= 0 or damage <= 0:
        return 0
    actual = min(health, int(damage))
    source_sess['battle_damage_dealt'] = int(source_sess.get(
        'battle_damage_dealt', 0)) + actual
    source_sess.setdefault('battle_damaged_vehicle_ids', set()).add(
        target_sess.get('account_id'))
    target_sess['battle_damage_received'] = int(target_sess.get(
        'battle_damage_received', 0)) + actual
    target_sess['battle_vehicle_health'] = max(0, health - actual)
    broadcast_vehicle_health(sock, target_sess, source_sess, actual)
    if target_sess['battle_vehicle_health'] <= 0:
        send_destroyed_vehicle_freeze(sock, target_sess)
    capture_drop_result = drop_invader_capture_on_damage(target_sess, source_sess)
    if capture_drop_result is not None:
        cap_updates, cap_sessions = capture_drop_result
        if cap_updates and cap_sessions:
            send_base_capture_updates(sock, cap_sessions, cap_updates)
    return actual


def process_ram_collision(sock, source_sess: dict,
                          target_sess: dict,
                          collision_info: dict) -> bool:
    if not RAM_DAMAGE_ENABLED:
        return False
    if int(source_sess.get('battle_vehicle_health') or 0) <= 0:
        return False
    if int(target_sess.get('battle_vehicle_health') or 0) <= 0:
        return False
    normal_x, normal_z = collision_info.get('normal') or (0.0, 1.0)
    if not ram_damage_contact_confirmed(source_sess, target_sess, collision_info):
        sync_ram_collision(sock, source_sess, target_sess, normal_x, normal_z)
        return False
    closing_speed = min(
        RAM_MAX_CLOSING_SPEED,
        ram_closing_speed(source_sess, target_sess, collision_info))
    if closing_speed < RAM_MIN_CLOSING_SPEED:
        sync_ram_collision(sock, source_sess, target_sess, normal_x, normal_z)
        return False
    now = time.time()
    if not claim_ram_cooldown(source_sess, target_sess, now):
        sync_ram_collision(sock, source_sess, target_sess, normal_x, normal_z)
        return False
    sync_ram_collision(sock, source_sess, target_sess, normal_x, normal_z,
                       push_target=True)
    same_team = source_sess.get('battle_team') == target_sess.get('battle_team')
    if same_team and not RAM_FRIENDLY_DAMAGE:
        return True
    damage_to_target, damage_to_source = compute_ram_damage(
        get_session_battle_vehicle(source_sess),
        get_session_battle_vehicle(target_sess),
        closing_speed)
    dealt_to_target = apply_contact_damage(
        sock, source_sess, target_sess, damage_to_target)
    dealt_to_source = apply_contact_damage(
        sock, target_sess, source_sess, damage_to_source)
    if dealt_to_target or dealt_to_source:
        print(f"    [ram] {source_sess.get('username') or 'player'} -> "
              f"{target_sess.get('username') or 'player'} "
              f"closing={closing_speed:.2f}m/s "
              f"damage={dealt_to_target}/{dealt_to_source}")
    if int(target_sess.get('battle_vehicle_health') or 0) <= 0:
        finish_battle_if_needed(sock, source_sess, target_sess)
    if (not source_sess.get('battle_ended') and
            int(source_sess.get('battle_vehicle_health') or 0) <= 0):
        finish_battle_if_needed(sock, target_sess, source_sess)
    return True


def process_pending_vehicle_collision(sock, sess: dict) -> bool:
    collision_info = sess.pop('battle_vehicle_collision_info', None)
    if not collision_info:
        return False
    target_sess = collision_info.get('session')
    if not isinstance(target_sess, dict):
        return False
    return process_ram_collision(sock, sess, target_sess, collision_info)


def broadcast_vehicle_shot_feedback(sock, target_sess: dict, source_sess: dict,
                                    hit_info: dict, effects_index: int,
                                    damage: int, shell: dict = None):
    effects_index = safe_effects_index(effects_index)
    if not ENABLE_CLIENT_SHOT_DAMAGE_EFFECTS:
        if damage > 0:
            broadcast_vehicle_health(sock, target_sess, source_sess, damage)
        return
    match_id = target_sess.get('battle_match_id')
    target_account_id = target_sess.get('account_id')
    source_account_id = source_sess.get('account_id')
    info = safe_dict(hit_info)
    center = safe_vec3(info.get('hitWorld'), (0.0, 0.0, 0.0))
    with battle_lock:
        viewers = [sess for sess in active_battle_accounts.values()
                   if sess.get('battle_match_id') == match_id]
    for viewer in viewers:
        addr = viewer.get('addr')
        if not addr:
            continue
        known = viewer.setdefault('known_remote_accounts', set())
        if viewer is not target_sess:
            force_feedback = force_shot_feedback_to_source(
                viewer, source_sess, target_sess)
            if not force_feedback:
                if not update_remote_vehicle_visibility(sock, viewer, target_sess):
                    continue
                if target_account_id not in known:
                    send_remote_vehicle(sock, viewer, target_sess)
        source_visible = True
        if viewer is not source_sess:
            source_visible = update_remote_vehicle_visibility(
                sock, viewer, source_sess)
            if source_visible and source_account_id not in known:
                send_remote_vehicle(sock, viewer, source_sess)
        target_id = viewer_vehicle_id(viewer, target_sess)
        attacker_id = viewer_vehicle_id(viewer, source_sess) if source_visible else 0
        if info.get('artillerySplash') and not info.get('artilleryDirectHit'):
            msg = build_vehicle_damage_from_explosion(
                target_id, attacker_id, center, effects_index)
        else:
            msg = build_vehicle_damage_from_shot(
                target_id, attacker_id, [pack_damage_segment(info)],
                effects_index)
        if damage > 0:
            msg += build_health_update_for_viewer(viewer, target_sess)
        send_avatar_messages(sock, addr, viewer, msg, '', reliable=True)


def viewer_vehicle_id(viewer_sess: dict, subject_sess: dict) -> int:
    if viewer_sess is subject_sess:
        return PLAYER_VEHICLE_ID
    return get_remote_vehicle_id(subject_sess)


def session_by_account_id(account_id, sessions):
    for sess in sessions:
        if sess.get('account_id') == account_id:
            return sess
    return None


def build_battle_results_for_viewer(viewer_sess: dict, sessions) -> dict:
    winner = bool(viewer_sess.get('battle_winner'))
    killed = []
    damaged = []
    spotted = []
    for account_id in viewer_sess.get('battle_killed_vehicle_ids', set()):
        target = session_by_account_id(account_id, sessions)
        if target is not None:
            killed.append(viewer_vehicle_id(viewer_sess, target))
    for account_id in viewer_sess.get('battle_damaged_vehicle_ids', set()):
        target = session_by_account_id(account_id, sessions)
        if target is not None:
            damaged.append(viewer_vehicle_id(viewer_sess, target))
    for account_id in viewer_sess.get('battle_spotted_vehicle_ids', set()):
        target = session_by_account_id(account_id, sessions)
        if target is not None:
            spotted.append(viewer_vehicle_id(viewer_sess, target))
    killer = session_by_account_id(viewer_sess.get('battle_killer_account_id'), sessions)
    killer_id = viewer_vehicle_id(viewer_sess, killer) if killer is not None else 0
    battle_start = float(viewer_sess.get('battle_start_wall',
                                         viewer_sess.get('battle_launch_wall', time.time())))
    life_time = max(0, int(time.time() - battle_start))
    return {
        'xp': 750 if winner else 250,
        'credits': 3000 if winner else 1000,
        'freeXP': 0,
        'xpFactor': 1,
        'repair': 0,
        'health': int(viewer_sess.get('battle_vehicle_health') or 0),
        'ammo': [],
        'crewActivityFlags': [],
        'vehicleID': PLAYER_VEHICLE_ID,
        'arenaUniqueID': int(viewer_sess.get('battle_match_id') or 0),
        'isWinner': winner,
        'bonusType': 1,
        'killerID': killer_id,
        'damaged': damaged,
        'killed': killed,
        'killedTypeCompDescrs': [0 for _ in killed],
        'spotted': spotted,
        'shots': int(viewer_sess.get('battle_shots', 0)),
        'hits': int(viewer_sess.get('battle_hits', 0)),
        'damageDealt': int(viewer_sess.get('battle_damage_dealt', 0)),
        'potentialDamageDealt': int(viewer_sess.get('battle_damage_dealt', 0)),
        'shotsReceived': int(viewer_sess.get('battle_shots_received', 0)),
        'damageReceived': int(viewer_sess.get('battle_damage_received', 0)),
        'potentialDamageReceived': int(viewer_sess.get('battle_damage_received', 0)),
        'capturePoints': int(viewer_sess.get('battle_capture_points', 0)),
        'droppedCapturePoints': int(viewer_sess.get(
            'battle_dropped_capture_points', 0)),
        'lifeTime': life_time,
        'arenaTypeID': normalize_arena_type_id(viewer_sess.get('battle_arena_type_id')),
        'arenaCreateTime': int(viewer_sess.get('battle_launch_wall', time.time())),
        'achieveIndices': [],
        'heroVehicleIDs': [],
        'epicAchievements': [],
        'honorTitles': [],
        'tkillRating': 0.0,
        'tkillLog': [],
        'xpPenalty': 0,
        'creditsPenalty': 0,
        'creditsContributionIn': 0,
        'creditsContributionOut': 0,
    }


def pack_int32_array(values) -> bytes:
    out = struct.pack('<i', len(values))
    for value in values:
        out += struct.pack('<i', int(value))
    return out


def pack_uint8_array(values) -> bytes:
    out = struct.pack('<i', len(values))
    for value in values:
        out += struct.pack('<B', int(value) & 0xff)
    return out


def pack_int16_array(values) -> bytes:
    out = struct.pack('<i', len(values))
    for value in values:
        out += struct.pack('<h', int(clamp(int(value), -32768, 32767)))
    return out


def pack_uint32_array(values) -> bytes:
    out = struct.pack('<i', len(values))
    for value in values:
        out += struct.pack('<I', int(value) & 0xffffffff)
    return out


def pack_uint64_array(values) -> bytes:
    out = struct.pack('<i', len(values))
    for value in values:
        out += struct.pack('<Q', int(value) & 0xffffffffffffffff)
    return out


def pack_bool_array(values) -> bytes:
    out = struct.pack('<i', len(values))
    for value in values:
        out += struct.pack('<B', 1 if value else 0)
    return out


def pack_tkill_log(values) -> bytes:
    out = struct.pack('<i', len(values))
    for value in values:
        out += struct.pack('<q', int(value.get('targetID', 0)))
        out += struct.pack('<B', 1 if value.get('isKill') else 0)
        out += struct.pack('<B', int(value.get('means', 0)) & 0xff)
        out += struct.pack('<I', int(value.get('creditsContribution', 0)) & 0xffffffff)
        out += struct.pack('<I', int(value.get('creditsPenalty', 0)) & 0xffffffff)
        out += struct.pack('<I', int(value.get('xpPenalty', 0)) & 0xffffffff)
    return out


def pack_battle_results_ext(results: dict) -> bytes:
    out = struct.pack('<B', 1)
    out += struct.pack('<I', int(results.get('xp', 0)) & 0xffffffff)
    out += struct.pack('<I', int(results.get('credits', 0)) & 0xffffffff)
    out += struct.pack('<I', int(results.get('freeXP', 0)) & 0xffffffff)
    out += struct.pack('<B', int(results.get('xpFactor', 1)) & 0xff)
    out += struct.pack('<I', int(results.get('repair', 0)) & 0xffffffff)
    out += struct.pack('<h', int(results.get('health', 0)))
    out += pack_int32_array(results.get('ammo', []))
    out += pack_bool_array(results.get('crewActivityFlags', []))
    out += struct.pack('<i', int(results.get('vehicleID', 0)))
    out += struct.pack('<Q', int(results.get('arenaUniqueID', 0)) & 0xffffffffffffffff)
    out += struct.pack('<B', 1 if results.get('isWinner') else 0)
    out += struct.pack('<B', int(results.get('bonusType', 1)) & 0xff)
    out += struct.pack('<i', int(results.get('killerID', 0)))
    out += pack_int32_array(results.get('killed', []))
    out += pack_uint32_array(results.get('killedTypeCompDescrs', []))
    out += pack_int32_array(results.get('damaged', []))
    out += pack_int32_array(results.get('spotted', []))
    out += struct.pack('<H', int(results.get('shots', 0)) & 0xffff)
    out += struct.pack('<H', int(results.get('hits', 0)) & 0xffff)
    out += struct.pack('<I', int(results.get('damageDealt', 0)) & 0xffffffff)
    out += struct.pack('<I', int(results.get('potentialDamageDealt', 0)) & 0xffffffff)
    out += struct.pack('<H', int(results.get('shotsReceived', 0)) & 0xffff)
    out += struct.pack('<I', int(results.get('damageReceived', 0)) & 0xffffffff)
    out += struct.pack('<I', int(results.get('potentialDamageReceived', 0)) & 0xffffffff)
    out += struct.pack('<H', int(results.get('capturePoints', 0)) & 0xffff)
    out += struct.pack('<H', int(results.get('droppedCapturePoints', 0)) & 0xffff)
    out += struct.pack('<H', int(results.get('lifeTime', 0)) & 0xffff)
    out += struct.pack('<I', int(results.get('arenaTypeID', ARENA_TYPE_KARELIA)) & 0xffffffff)
    out += struct.pack('<I', int(results.get('arenaCreateTime', 0)) & 0xffffffff)
    out += pack_uint8_array(results.get('achieveIndices', []))
    out += pack_int32_array(results.get('heroVehicleIDs', []))
    out += pack_uint8_array(results.get('epicAchievements', []))
    out += pack_uint32_array(results.get('honorTitles', []))
    out += struct.pack('<f', float(results.get('tkillRating', 0.0)))
    out += pack_tkill_log(results.get('tkillLog', []))
    out += struct.pack('<i', int(results.get('xpPenalty', 0)))
    out += struct.pack('<i', int(results.get('creditsPenalty', 0)))
    out += struct.pack('<i', int(results.get('creditsContributionIn', 0)))
    out += struct.pack('<i', int(results.get('creditsContributionOut', 0)))
    return out


def build_avatar_on_vehicle_left_arena(is_active_vehicle: bool,
                                       veh_inv_id: int,
                                       results: dict) -> bytes:
    em = struct.pack('<I', AVATAR_ENTITY_ID)
    em += struct.pack('<B', 1 if is_active_vehicle else 0)
    em += struct.pack('<i', int(veh_inv_id))
    em += pack_battle_results_ext(results)
    return msg_varlen(AVATAR_ON_VEHICLE_LEFT_ARENA_MSG_ID, em)


def send_delayed_battle_results(sock, viewer_sess: dict, sessions,
                                winner_display_team: int, reason: int,
                                period_end_time: int):
    addr = viewer_sess.get('addr')
    if not addr:
        return
    results = build_battle_results_for_viewer(viewer_sess, sessions)
    veh_inv_id = int(viewer_sess.get('battle_vehicle_inv_id') or 0)
    account_id = int(viewer_sess.get('account_id') or 0)
    battle_id  = int(viewer_sess.get('battle_id') or 0)
    if account_id and battle_id and not viewer_sess.get('battle_results_persisted'):
        try:
            record_battle_result(battle_id, account_id, veh_inv_id, results)
            viewer_sess['battle_results_persisted'] = True
            invalidate_sync_cache(account_id)
        except Exception as exc:
            print(f"[!] record_battle_result(account={account_id}, "
                  f"battle={battle_id}) failed: {exc}")
    msg = build_avatar_on_vehicle_left_arena(
        True, veh_inv_id, results)
    send_avatar_messages(sock, addr, viewer_sess, msg,
                         "Battle results",
                         reliable=True)
    send_afterbattle_period(sock, viewer_sess, winner_display_team,
                            reason, period_end_time)


def send_afterbattle_period(sock, viewer_sess: dict, winner_display_team: int,
                            reason: int, period_end_time: int):
    addr = viewer_sess.get('addr')
    if not addr:
        return
    msg = build_avatar_update_arena(
        ARENA_UPDATE_PERIOD,
        (ARENA_PERIOD_AFTERBATTLE, period_end_time, 30,
         (winner_display_team, reason)))
    send_avatar_messages(sock, addr, viewer_sess, msg,
                         "Avatar.updateArena(PERIOD=AFTERBATTLE)",
                         reliable=True)


def send_afterbattle_music_nudge(sock, viewer_sess: dict,
                                 winner_display_team: int, reason: int,
                                 period_end_time: int):
    addr = viewer_sess.get('addr')
    if not addr:
        return
    now = current_server_time(viewer_sess)
    msg = build_avatar_update_arena(
        ARENA_UPDATE_PERIOD,
        (ARENA_PERIOD_BATTLE, now + 1, 1, None))
    send_avatar_messages(sock, addr, viewer_sess, msg,
                         "Avatar.updateArena(PERIOD=BATTLE music nudge)",
                         reliable=True)
    runtime_call_later(
        0.2,
        lambda: send_afterbattle_period(sock, viewer_sess,
                                        winner_display_team, reason,
                                        period_end_time))


def finish_battle_by_base_capture(sock, match_id, winner_team: int,
                                  captured_base_team: int):
    with battle_lock:
        sessions = [sess for sess in active_battle_accounts.values()
                    if sess.get('battle_match_id') == match_id]
        if not sessions or any(sess.get('battle_ended') for sess in sessions):
            return False
        for sess in sessions:
            sess['battle_ended'] = True
            sess['battle_period_active'] = False
            sess['battle_motion_flags'] = 0
            sess['battle_speed'] = 0.0
            sess['battle_rspeed'] = 0.0
            sess['battle_winner'] = sess.get('battle_team') == winner_team
    now = current_server_time(sessions[0])
    period_end_time = now + 30
    for viewer in sessions:
        addr = viewer.get('addr')
        if not addr:
            continue
        winner_display_team = 1 if viewer.get('battle_team') == winner_team else 2
        msgs = b''
        msgs += build_avatar_update_arena(ARENA_UPDATE_BASE_CAPTURED,
                                          int(captured_base_team))
        msgs += build_avatar_update_arena(
            ARENA_UPDATE_PERIOD,
            (ARENA_PERIOD_AFTERBATTLE, period_end_time, 30,
             (winner_display_team, BATTLE_FINISH_REASON_BASE)))
        send_avatar_messages(sock, addr, viewer, msgs,
                             "Battle finished by base capture",
                             reliable=True)
        runtime_call_later(
            0.75,
            lambda v=viewer, w=winner_display_team, t=period_end_time:
                send_afterbattle_music_nudge(sock, v, w,
                                             BATTLE_FINISH_REASON_BASE, t))
        timer_sessions = list(sessions)
        runtime_call_later(
            1.5,
            lambda v=viewer, ss=timer_sessions, w=winner_display_team, t=period_end_time:
                send_delayed_battle_results(sock, v, ss, w,
                                            BATTLE_FINISH_REASON_BASE, t))
    print(f"    [battle] finished match={match_id} winnerTeam={winner_team} reason=base")
    persisted_battle_ids = set()
    for sess in sessions:
        bid = int(sess.get('battle_id') or 0)
        if bid and bid not in persisted_battle_ids:
            persisted_battle_ids.add(bid)
            try:
                mark_battle_finished(bid, winner_team,
                                     BATTLE_FINISH_REASON_BASE)
            except Exception as exc:
                print(f"[!] mark_battle_finished({bid}) failed: {exc}")
    return True


def process_base_capture(sock, match_id):
    with battle_lock:
        sessions = [sess for sess in active_battle_accounts.values()
                    if sess.get('battle_match_id') == match_id and
                    sess.get('battle_bundle_sent') and
                    sess.get('battle_period_active')]
    if not sessions or any(sess.get('battle_ended') for sess in sessions):
        return
    state = ensure_battle_capture_state(sessions[0])
    for sess in sessions[1:]:
        sess['battle_capture_state'] = state
    state_lock = state.get('lock')
    updates = []
    finish_args = None
    lock_ctx = state_lock if state_lock is not None else contextlib.nullcontext()
    with lock_ctx:
        now = time.time()
        dt = max(0.001, min(0.5, now - float(state.get('last_update', now))))
        state['last_update'] = now
        debug_now = time.time()
        debug_last = float(state.get('_debug_last_log', 0.0))
        debug_emit = (debug_now - debug_last) >= 10.0
        if debug_emit:
            state['_debug_last_log'] = debug_now
        for base_team, base in state.get('bases', {}).items():
            base_team = int(base_team)
            base_pos = base.get('pos') or (0.0, 0.0)
            base_radius = float(base.get('radius', BASE_CAPTURE_RADIUS))
            invader_progress = base.setdefault('invader_progress', {})
            active_invaders = []
            active_invader_ids = set()
            defenders_present = False
            for sess in sessions:
                if int(sess.get('battle_vehicle_health') or 0) <= 0:
                    continue
                pos = get_base_capture_vehicle_pos(sess)
                d = distance_xz(pos, base_pos)
                if debug_emit:
                    print(f"    [capture-dbg] match={match_id} "
                          f"player='{sess.get('username')}' "
                          f"team={sess.get('battle_team')} "
                          f"pos=({pos[0]:.1f},{pos[2]:.1f}) "
                          f"baseTeam={base_team} "
                          f"basePos=({base_pos[0]:.1f},{base_pos[1]:.1f}) "
                          f"dist={d:.1f}m radius={base_radius:.1f}m "
                          f"in_circle={'yes' if d <= base_radius else 'no'}")
                if d > base_radius:
                    continue
                if int(sess.get('battle_team') or 0) == base_team:
                    defenders_present = True
                else:
                    active_invaders.append(sess)
                    active_invader_ids.add(sess.get('account_id'))
            for account_id in list(invader_progress.keys()):
                if account_id not in active_invader_ids:
                    invader_progress.pop(account_id, None)
            capturing_team = 0
            if active_invaders and not defenders_present:
                capturing_team = int(active_invaders[0].get('battle_team') or 0)
                n = len(active_invaders)
                total_rate = min(BASE_CAPTURE_MAX_POINTS_PER_SECOND,
                                 n * BASE_CAPTURE_POINTS_PER_SECOND)
                per_rate = total_rate / float(n)
                for invader in active_invaders:
                    account_id = invader.get('account_id')
                    prev = float(invader_progress.get(account_id, 0.0))
                    new_personal = min(float(BASE_CAPTURE_POINTS_MAX),
                                       prev + per_rate * dt)
                    invader_progress[account_id] = new_personal
                    gained = int(new_personal) - int(prev)
                    if gained > 0:
                        invader['battle_capture_points'] = int(
                            invader.get('battle_capture_points', 0)) + gained
            elif active_invaders and defenders_present:
                capturing_team = int(base.get('capturing_team') or 0)
            total_points = (sum(invader_progress.values())
                            if invader_progress else 0.0)
            total_points = min(float(BASE_CAPTURE_POINTS_MAX), total_points)
            base['points'] = total_points
            base['capturing_team'] = capturing_team
            sent_points = max(0, min(BASE_CAPTURE_POINTS_MAX,
                                     int(total_points)))
            if sent_points != int(base.get('last_sent', -1)):
                base['last_sent'] = sent_points
                print(f"    [battle] base capture team={capturing_team} "
                      f"baseTeam={base_team} points={sent_points} "
                      f"invaders={len(active_invaders)} "
                      f"defenders={'yes' if defenders_present else 'no'}")
                updates.append((base_team,
                                int(base.get('base_id') or base_team),
                                sent_points))
            if (total_points >= BASE_CAPTURE_POINTS_MAX and capturing_team
                    and not defenders_present):
                finish_args = (capturing_team, base_team)
                break
    if updates:
        send_base_capture_updates(sock, sessions, updates)
    if finish_args is not None:
        finish_battle_by_base_capture(sock, match_id,
                                      finish_args[0], finish_args[1])


def drop_invader_capture_on_damage(target_sess: dict, source_sess: dict = None):
    """Drop the target's accumulated capture progress on any base.

    Called from `apply_shot_damage` when a vehicle takes damage.  In WoT 0.6.5
    this is the primary way defenders interrupt a capture: shooting the
    invader resets their personal contribution to 0 and credits the shooter
    with `droppedCapturePoints` equal to the dropped amount."""
    state = target_sess.get('battle_capture_state')
    if not state:
        return None, None
    target_account_id = target_sess.get('account_id')
    if target_account_id is None:
        return None, None
    state_lock = state.get('lock')
    lock_ctx = state_lock if state_lock is not None else contextlib.nullcontext()
    total_dropped = 0
    affected_bases = []
    with lock_ctx:
        for base_team, base in state.get('bases', {}).items():
            progress = base.get('invader_progress')
            if not progress or target_account_id not in progress:
                continue
            dropped_personal = float(progress.pop(target_account_id))
            if dropped_personal <= 0.0:
                continue
            total_dropped += int(dropped_personal)
            new_total = sum(progress.values()) if progress else 0.0
            new_total = min(float(BASE_CAPTURE_POINTS_MAX), new_total)
            base['points'] = new_total
            if not progress:
                base['capturing_team'] = 0
            sent_total = max(0, min(BASE_CAPTURE_POINTS_MAX, int(new_total)))
            base['last_sent'] = sent_total
            affected_bases.append((int(base_team),
                                   int(base.get('base_id') or base_team),
                                   sent_total))
    if total_dropped > 0 and source_sess is not None:
        source_sess['battle_dropped_capture_points'] = int(
            source_sess.get('battle_dropped_capture_points', 0)) + total_dropped
    if affected_bases:
        with battle_lock:
            match_id = target_sess.get('battle_match_id')
            sessions = [sess for sess in active_battle_accounts.values()
                        if sess.get('battle_match_id') == match_id and
                        sess.get('battle_bundle_sent') and
                        sess.get('battle_period_active')]
        if sessions:
            print(f"    [battle] capture interrupted "
                  f"target={target_sess.get('username')} "
                  f"dropped={total_dropped} "
                  f"shooter={source_sess.get('username') if source_sess else None}")
            return list(affected_bases), sessions
    return None, None


def send_base_capture_updates(sock, sessions, updates):
    for viewer in sessions:
        addr = viewer.get('addr')
        if not addr:
            continue
        msgs = b''
        for update in updates:
            base_team, base_id, points = update
            msgs += build_avatar_update_arena(
                ARENA_UPDATE_BASE_POINTS,
                (int(base_team), int(base_id), int(points)))
        send_avatar_messages(sock, addr, viewer, msgs,
                             "Avatar.updateArena(BASE_POINTS)",
                             reliable=True)


def finish_battle_if_needed(sock, source_sess: dict, target_sess: dict):
    match_id = source_sess.get('battle_match_id')
    with battle_lock:
        sessions = [sess for sess in active_battle_accounts.values()
                    if sess.get('battle_match_id') == match_id]
    if not sessions or any(sess.get('battle_ended') for sess in sessions):
        return
    alive_teams = set()
    for sess in sessions:
        if int(sess.get('battle_vehicle_health') or 0) > 0:
            alive_teams.add(sess.get('battle_team'))
    if len(alive_teams) > 1:
        return
    winner_team = next(iter(alive_teams), source_sess.get('battle_team'))
    now = current_server_time(source_sess)
    victim_account_id = target_sess.get('account_id')
    killer_account_id = source_sess.get('account_id')
    source_sess['battle_frags'] = int(source_sess.get('battle_frags', 0)) + 1
    source_sess.setdefault('battle_killed_vehicle_ids', set()).add(victim_account_id)
    target_sess['battle_killer_account_id'] = killer_account_id
    for sess in sessions:
        sess['battle_ended'] = True
        sess['battle_period_active'] = False
        sess['battle_motion_flags'] = 0
        sess['battle_speed'] = 0.0
        sess['battle_rspeed'] = 0.0
        sess['battle_winner'] = sess.get('battle_team') == winner_team
    for viewer in sessions:
        addr = viewer.get('addr')
        if not addr:
            continue
        victim_id = viewer_vehicle_id(viewer, target_sess)
        killer_id = viewer_vehicle_id(viewer, source_sess)
        winner_display_team = 1 if viewer.get('battle_team') == winner_team else 2
        msgs = b''
        msgs += build_avatar_update_arena(ARENA_UPDATE_VEHICLE_KILLED,
                                          (victim_id, killer_id, 0))
        msgs += build_avatar_update_arena(ARENA_UPDATE_VEHICLE_STATISTICS,
                                          (killer_id, int(source_sess.get('battle_frags', 0))))
        msgs += build_avatar_update_arena(
            ARENA_UPDATE_PERIOD,
            (ARENA_PERIOD_AFTERBATTLE, now + 30, 30,
             (winner_display_team, BATTLE_FINISH_REASON_EXTERMINATION)))
        send_avatar_messages(sock, addr, viewer, msgs,
                             "Battle finished",
                             reliable=True)
        runtime_call_later(
            0.75,
            lambda v=viewer, w=winner_display_team, t=now + 30:
                send_afterbattle_music_nudge(sock, v, w,
                                             BATTLE_FINISH_REASON_EXTERMINATION, t))
        timer_sessions = list(sessions)
        runtime_call_later(
            1.5,
            lambda v=viewer, ss=timer_sessions, w=winner_display_team, t=now + 30:
                send_delayed_battle_results(sock, v, ss, w,
                                            BATTLE_FINISH_REASON_EXTERMINATION, t))
    print(f"    [battle] finished match={match_id} winnerTeam={winner_team}")
    persisted_battle_ids = set()
    for sess in sessions:
        bid = int(sess.get('battle_id') or 0)
        if bid and bid not in persisted_battle_ids:
            persisted_battle_ids.add(bid)
            try:
                mark_battle_finished(bid, winner_team,
                                     BATTLE_FINISH_REASON_EXTERMINATION)
            except Exception as exc:
                print(f"[!] mark_battle_finished({bid}) failed: {exc}")


def resolve_current_shot(source_sess: dict, shell: dict, shot_pos, shot_vec):
    if source_sess.get('battle_last_shot_forced_miss'):
        print("    [shot] miss: dispersion roll")
        return None
    try:
        hit_info = find_shot_target(source_sess, shot_pos, shot_vec)
    except Exception as exc:
        print(f"    [shot] miss: target resolution failed: {exc}")
        return None
    if hit_info is None:
        return None
    try:
        return resolve_shot_armor(shell, hit_info)
    except Exception as exc:
        print(f"    [armor] resolve failed: {exc}")
        return None


def apply_resolved_shot_damage(sock, source_sess: dict, shell: dict,
                               resolved: dict):
    if resolved is None:
        return None, 0, None
    target = resolved.get('target')
    if not isinstance(target, dict):
        return None, 0, None
    health = int(target.get('battle_vehicle_health') or get_vehicle_max_health(
        get_session_battle_vehicle(target)))
    if health <= 0:
        return None, 0, None
    damage = min(health, int(resolved.get('damage') or 0))
    source_sess['battle_hits'] = int(source_sess.get('battle_hits', 0)) + 1
    target['battle_shots_received'] = int(target.get('battle_shots_received', 0)) + 1
    if damage > 0:
        source_sess['battle_damage_dealt'] = int(source_sess.get(
            'battle_damage_dealt', 0)) + damage
        source_sess.setdefault('battle_damaged_vehicle_ids', set()).add(target.get('account_id'))
        target['battle_damage_received'] = int(target.get(
            'battle_damage_received', 0)) + damage
        target['battle_vehicle_health'] = max(0, health - damage)
        if target['battle_vehicle_health'] <= 0:
            send_destroyed_vehicle_freeze(sock, target)
    result_name = {
        SHOT_RESULT_RICOCHET: 'ricochet',
        SHOT_RESULT_ARMOR_NOT_PIERCED: 'not_pierced',
        SHOT_RESULT_ARMOR_PIERCED_NO_DAMAGE: 'pierced_no_damage',
        SHOT_RESULT_ARMOR_PIERCED: 'pierced',
        SHOT_RESULT_CRITICAL_HIT: 'critical',
    }.get(int(resolved.get('result') or 0), 'unknown')
    print(f"    [armor] {source_sess.get('username') or 'player'} -> "
          f"{target.get('username') or 'player'} {result_name} "
          f"zone={resolved.get('component')}/{resolved.get('zone')} "
          f"pen={float(resolved.get('penetration') or 0.0):.1f} "
          f"armor={float(resolved.get('armor') or 0.0):.1f} "
          f"eff={float(resolved.get('effectiveArmor') or 0.0):.1f} "
          f"angle={float(resolved.get('impactAngleDeg') or 0.0):.1f} "
          f"damage={damage}")
    effects_index = safe_effects_index((shell or {}).get('effectsIndex', 0))
    broadcast_vehicle_shot_feedback(sock, target, source_sess, resolved,
                                    effects_index, damage, shell)
    if damage > 0:
        capture_drop_result = drop_invader_capture_on_damage(target, source_sess)
        if capture_drop_result is not None:
            cap_updates, cap_sessions = capture_drop_result
            if cap_updates and cap_sessions:
                send_base_capture_updates(sock, cap_sessions, cap_updates)
        if target['battle_vehicle_health'] <= 0:
            finish_battle_if_needed(sock, source_sess, target)
    return target, damage, resolved


def apply_shot_damage(sock, source_sess: dict, shell: dict, shot_pos, shot_vec):
    resolved = resolve_current_shot(source_sess, shell, shot_pos, shot_vec)
    return apply_resolved_shot_damage(sock, source_sess, shell, resolved)


def get_projectile_impact_pos(sess: dict, shot_pos, shot_vec, resolved: dict):
    if resolved and resolved.get('hitWorld'):
        return resolved.get('hitWorld')
    marker_pos = safe_vec3(sess.get('battle_last_shot_target_pos'), None)
    if marker_pos is not None and is_artillery_session(sess):
        return marker_pos
    return (
        shot_pos[0] + shot_vec[0] * SHOT_TRACE_DISTANCE,
        shot_pos[1] + shot_vec[1] * SHOT_TRACE_DISTANCE,
        shot_pos[2] + shot_vec[2] * SHOT_TRACE_DISTANCE,
    )


def handle_vehicle_shot(sock, addr, sess: dict, log_blocked: bool = False):
    if sess.get('battle_ended'):
        return True
    if is_destroyed_vehicle_session(sess):
        send_destroyed_vehicle_freeze(sock, sess)
        return True
    msgs, reload_time, shell_cd, fired = build_vehicle_shot_messages(sess)
    if not fired:
        if log_blocked:
            print(f"    [>] Vehicle.shot blocked(shell={shell_cd}, reload={reload_time:.2f})")
        return True
    mark_vehicle_shot_visibility_penalty(sess)
    own_visual_sent = send_avatar_messages(
        sock, addr, sess, msgs,
        f"Vehicle.shot(shell={shell_cd}, reload={reload_time:.2f})",
        reliable=True)
    shot_info = sess.get('battle_last_shot_info')
    if shot_info:
        ensure_shot_visual_viewers(sess, shot_info[0])
        if own_visual_sent:
            remember_shot_visual_viewer(sess, shot_info[0], sess)
        broadcast_remote_vehicle_shot(sock, sess, *shot_info)
        shell = sess.get('battle_last_shot_shell') or {}
        visual_shot_vec = sess.get('battle_last_shot_vec') or get_session_shot_direction(sess)
        visual_shot_vec = normalize_vec(visual_shot_vec)
        server_shot_info = sess.get('battle_last_server_shot_info') or shot_info
        server_shot_vec = sess.get('battle_last_server_shot_vec') or visual_shot_vec
        server_shot_vec = normalize_vec(server_shot_vec)
        if is_artillery_session(sess):
            resolved = resolve_current_shot(sess, shell, server_shot_info[1],
                                            server_shot_vec)
            impact_pos = get_projectile_impact_pos(sess, server_shot_info[1],
                                                   server_shot_vec, resolved)
            flight_time = sess.get('battle_last_visual_flight_time')
            if flight_time is None:
                flight_time = estimate_projectile_flight_time(
                    shot_info[1], impact_pos, shot_info[2])

            def _artillery_impact():
                if sess.get('battle_ended'):
                    return
                apply_resolved_shot_damage(sock, sess, shell, resolved)
                broadcast_projectile_impact(sock, sess, shot_info[0],
                                            shot_info[4], impact_pos,
                                            visual_shot_vec)

            runtime_call_later(flight_time, _artillery_impact)
        else:
            _target, _damage, resolved = apply_shot_damage(
                sock, sess, shell, server_shot_info[1], server_shot_vec)
            impact_pos = get_projectile_impact_pos(sess, shot_info[1],
                                                   visual_shot_vec, resolved)
            impact_delay = estimate_direct_projectile_impact_delay(
                shot_info[1], impact_pos, shot_info[2])

            def _direct_impact():
                broadcast_projectile_impact(sock, sess, shot_info[0],
                                            shot_info[4], impact_pos,
                                            visual_shot_vec)

            runtime_call_later(impact_delay, _direct_impact)

    def _reload_done():
        if time.time() + 0.05 < float(sess.get('battle_reload_until', 0.0)):
            return
        send_avatar_messages(sock, addr, sess,
                             build_avatar_update_vehicle_reload(0.0),
                             "Vehicle.reload.done",
                             reliable=True)

    runtime_call_later(max(0.05, reload_time), _reload_done)
    return True


def build_leave_arena_to_hangar_bundle(account_name: str,
                                       database_id: int) -> bytes:
    """Avatar -> Account transition bundle (no per-session header msgs).

    Mirrors the tail of `build_init_bundle` (without authenticate/bandwidth/
    updateFrequency/setGameTime which are session-level): resetEntities(False)
    -> createBasePlayer(Account) -> Account.showGUI(lobby ctx).  The client's
    PlayerAvatar.onLeaveWorld() runs on entitiesReset, then Account.onBecome
    Player + showGUI bring the Hangar UI back."""
    msgs = b''

    # 0x04 resetEntities(keepPlayer=False) - drops Avatar and any AoI vehicles
    msgs += msg_fixed(0x04, struct.pack('<B', 0))

    # 0x05 createBasePlayer(Account) - same Account properties as init bundle
    server_settings_pickle = pickle.dumps({
        'vivoxDomain':  '',
        'vivoxIssuer':  '',
        'voipDomain':   '',
        'serverUTC':    0,
    }, protocol=0)
    account_name_bytes = (account_name or 'player').encode('utf-8', 'ignore') \
        or b'player'
    props  = bw_pack_string(account_name_bytes)
    props += bw_pack_string(account_name_bytes.lower())
    props += bw_pack_string(server_settings_pickle)
    cbp_payload = struct.pack('<I', PLAYER_ENTITY_ID) + \
                  struct.pack('<H', ACCOUNT_ENTITY_TYPE) + \
                  props
    msgs += msg_varlen(0x05, cbp_payload)

    # Account.showGUI(ctx) - WindowsManager.showLobby -> CommonPage.processLobby
    showgui_ctx = (
        f"(dp0\nS'databaseID'\np1\nL{int(database_id)}L\n"
        "sS'serverUTC'\np2\nL0L\ns."
    ).encode('ascii')
    em = struct.pack('<I', PLAYER_ENTITY_ID) + bw_pack_string(showgui_ctx)
    msgs += msg_varlen(ACCOUNT_SHOWGUI_MSG_ID, em)

    return msgs


def send_player_back_to_hangar(sock, addr, sess: dict):
    """Handle Avatar.base.leaveArena() - migrate the proxy back to Account.

    Idempotent: silently no-ops if the session has no battle bundle.  Sends
    entitiesReset(False)+createBasePlayer(Account)+showGUI then resets all
    battle/match state so the client's Hangar (and the matchmaker) see a
    clean account again."""
    if not sess.get('battle_bundle_sent'):
        return
    if not addr:
        return

    account_name = sess.get('username') or 'player'
    database_id = sess.get('account_id') or 1
    msgs = build_leave_arena_to_hangar_bundle(account_name, database_id)
    if not send_avatar_messages(sock, addr, sess, msgs,
                                "leaveArena -> hangar "
                                "(entitiesReset+createBasePlayer Account+showGUI)",
                                reliable=True):
        return

    account_id = sess.get('account_id')
    with battle_lock:
        active_battle_accounts.pop(account_id, None)
    dequeue_from_matchmaking(sess)

    sess['battle_bundle_sent'] = False
    sess['battle_ended'] = True
    sess['battle_period_active'] = False
    sess['battle_period_timer_started'] = False
    sess['battle_late_period_timer_started'] = False
    sess['battle_motion_loop_started'] = False
    sess['battle_match_id'] = None
    sess['avatar_ready_sent'] = False
    sess['battle_client_ready'] = False
    sess['battle_motion_flags'] = 0
    sess['battle_speed'] = 0.0
    sess['battle_rspeed'] = 0.0
    sess['battle_target_speed'] = 0.0
    sess['battle_target_rspeed'] = 0.0
    sess['known_remote_accounts'] = set()
    sess['battle_visual_shot_viewers'] = {}
    sess['queued_for_battle'] = False
    sess['sync_data_stream_sent'] = False
    sess['stat_cmd_count'] = 0
    sess['avatar_update_count'] = 0
    sess['battle_input_count'] = 0

    broadcast_account_server_counters(sock)


def handle_avatar_base_method(sock, addr, sess, msg_id: int, payload: bytes):
    req_id = parse_exposed_request_id(payload)

    if msg_id in AVATAR_BASE_METHOD_LEAVE_ARENA_IDS:
        print(f"    [avatar] leaveArena msgID=0x{msg_id:02x} req={req_id} payload={payload.hex()}")
        send_player_back_to_hangar(sock, addr, sess)
        return True

    if msg_id in AVATAR_BASE_METHOD_ON_CLIENT_READY_IDS:
        print(f"    [avatar] onClientReady req={req_id}")
        send_avatar_ready_and_prebattle(sock, addr, sess)
        return True

    if msg_id in AVATAR_BASE_METHOD_VEHICLE_MOVE_WITH_IDS:
        if len(payload) >= 5:
            flags = payload[4]
        elif len(payload) >= 1:
            flags = payload[0]
        else:
            return True
        if is_destroyed_vehicle_session(sess):
            send_destroyed_vehicle_freeze(sock, sess)
            return True
        prev_flags = sess.get('battle_motion_flags', 0)
        was_idle = (
            prev_flags == 0 and
            abs(float(sess.get('battle_speed', 0.0))) < 0.01 and
            abs(float(sess.get('battle_rspeed', 0.0))) < 0.01
        )
        sess['battle_motion_flags'] = flags
        if flags != 0 and was_idle:
            sess['battle_last_motion_time'] = time.time()
        move_count = sess.get('battle_move_update_count', 0) + 1
        sess['battle_move_update_count'] = move_count
        if BATTLE_VERBOSE_DEBUG and flags != prev_flags:
            mode = "server authoritative" if sess.get(
                'server_vehicle_authoritative', True) else "client controlled"
            vehicle = get_session_battle_vehicle(sess)
            move, turn = battle_motion_targets(flags, vehicle)
            current_speed = float(sess.get('battle_speed', 0.0))
            print(f"    [>] Vehicle.input(flags=0x{prev_flags:02x}->0x{flags:02x}) "
                  f"[{mode}, tank={(vehicle or {}).get('name', 'unknown')}, "
                  f"target=({move:.2f}m/s {move * 3.6:.1f}km/h,"
                  f"{turn:.2f}), current={current_speed * 3.6:.1f}km/h]")
        if (FORCED_POSITION_ON_FIRST_MOTION and
                sess.get('server_vehicle_authoritative', True) and
                sess.get('battle_period_active') and
                prev_flags == 0 and flags != 0 and
                not sess.get('battle_forced_position_sent_for_motion')):
            sess['battle_forced_position_sent_for_motion'] = True
            pos = sess.get('battle_pos', ARENA_SPAWN_POS[ARENA_TYPE_KARELIA])
            yaw = float(sess.get('battle_yaw', 0.0))
            send_avatar_messages(
                sock, addr, sess,
                build_forced_position(PLAYER_VEHICLE_ID, pos, yaw,
                                      space_id=SPACE_ID, vehicle_id=0),
                "Vehicle.forced_position (filter reset on first motion)",
                reliable=True)
        if not sess.get('server_vehicle_authoritative', True):
            return True
        return True

    if msg_id in AVATAR_BASE_METHOD_TRACK_POINT_WITH_GUN_IDS:
        if is_destroyed_vehicle_session(sess):
            send_destroyed_vehicle_freeze(sock, sess)
            return True
        target_pos = parse_vector3_exposed(payload)
        if target_pos is None:
            return True
        now = time.time()
        if now - float(sess.get('last_targeting_update', 0.0)) < TARGETING_UPDATE_INTERVAL:
            return True
        sess['last_targeting_update'] = now
        msgs = build_targeting_for_point(sess, target_pos)
        send_avatar_messages(
            sock, addr, sess, msgs,
            '',
            reliable=False)
        broadcast_remote_vehicle_position(sock, sess, force=True)
        return True

    if msg_id in AVATAR_BASE_METHOD_STOP_TRACKING_WITH_GUN_IDS:
        if is_destroyed_vehicle_session(sess):
            send_destroyed_vehicle_freeze(sock, sess)
            return True
        pos = sess.get('battle_pos', ARENA_SPAWN_POS[ARENA_TYPE_KARELIA])
        yaw = sess.get('battle_yaw', 0.0)
        target_pos = (pos[0] + math.sin(yaw) * 100.0,
                      pos[1] + 2.0,
                      pos[2] + math.cos(yaw) * 100.0)
        msgs = build_targeting_for_point(sess, target_pos)
        send_avatar_messages(sock, addr, sess,
                             msgs,
                             "Avatar.stopTrackingWithGun -> forward marker",
                             reliable=False)
        broadcast_remote_vehicle_position(sock, sess, force=True)
        return True

    if msg_id in AVATAR_BASE_METHOD_VEHICLE_SHOOT_IDS and len(payload) == 0:
        return handle_vehicle_shot(sock, addr, sess, log_blocked=True)

    if msg_id in AVATAR_BASE_METHOD_CHANGE_SETTING_IDS or msg_id == 0xc4:
        parsed = parse_vehicle_change_setting(payload)
        if parsed is None:
            return True
        code, value = parsed
        stock = sess.get('battle_ammo_stock') or build_vehicle_ammo_stock(
            get_session_battle_vehicle(sess))
        if int(stock.get(int(value), 0)) > 0:
            if code == 0:
                sess['battle_current_shell'] = value
            elif code == 1:
                sess['battle_next_shell'] = value
            send_avatar_messages(sock, addr, sess,
                                 build_avatar_update_vehicle_setting(code, value),
                                 f"Vehicle.setting(code={code}, value={value})",
                                 reliable=False)
        return True

    if msg_id in AVATAR_BASE_METHOD_VEHICLE_SHOOT_IDS:
        return handle_vehicle_shot(sock, addr, sess)

    if msg_id in (AVATAR_BASE_METHOD_TELEPORT_IDS |
                  AVATAR_BASE_METHOD_USE_HORN_IDS):
        print(f"    [avatar] method=0x{msg_id:02x} req={req_id} "
              f"payload={payload.hex()}")
        return True

    return False


def handle_account_doCmd(sock, addr, sess, msg_id: int, payload: bytes):
    """Р РѕР·РіР°Р»СѓР¶РµРЅРЅСЏ Р·Р° cmd:
       - cmd=300 (CMD_SYNC_SHOP)        в†’ stream Р· shop dict
       - cmd=700 (CMD_ENQUEUE_FOR_ARENA) в†’ onEnqueued event (no response)
       - cmd=701 (CMD_DEQUEUE)           в†’ onDequeued event (no response)
       - С–РЅС€С–                            в†’ onCmdResponseExt(success, full_sync)"""
    req_id, cmd = parse_doCmd_request(msg_id, payload)
    if req_id is None:
        return
    if cmd in (CMD_REQ_ARENA_LIST, CMD_REQ_SERVER_STATS, CMD_REQ_QUEUE_INFO):
        stat_cmd_count = sess.get('stat_cmd_count', 0) + 1
        sess['stat_cmd_count'] = stat_cmd_count
        if stat_cmd_count % 50 == 1:
            print(f"    [doCmd] msg=0x{msg_id:02x} reqID={req_id} cmd={cmd} x{stat_cmd_count}")
    else:
        print(f"    [doCmd] msg=0x{msg_id:02x} reqID={req_id} cmd={cmd}")

    if cmd == CMD_SYNC_DATA:
        if sess.get('sync_data_stream_sent'):
            account_id = int(sess.get('account_id') or 0)
            current_rev = get_account_sync_revision(account_id)
            requested_rev = parse_sync_requested_revision(payload)
            if requested_rev != current_rev:
                send_sync_stream(sock, addr, sess, req_id)
                print(f"    [>] syncData STREAM: req={req_id}, "
                      f"clientRev={requested_rev}, serverRev={current_rev}")
                return
            msg = build_oncmdrespext(req_id, RES_SUCCESS, make_empty_sync_pickle(current_rev))
            if not send_avatar_messages(sock, addr, sess, msg, '', reliable=True):
                return
            print(f"    [>] onCmdResponseExt(req={req_id}, res=0, ext=empty_sync rev={current_rev})")
            return
        sess['sync_data_stream_sent'] = True
        send_sync_stream(sock, addr, sess, req_id)
        return

    if cmd == CMD_SYNC_SHOP:
        send_shop_stream(sock, addr, sess, req_id)
        return

    if cmd == CMD_BUY_ITEM:
        args = parse_doCmd_int3(payload) or (0, 0, 1)
        _cache_rev, item_compact_descr, count = args
        account_id = int(sess.get('account_id') or 0)
        result = buy_item_for_account(account_id, item_compact_descr, count)
        if result.get('success'):
            invalidate_sync_cache(account_id)
            msg = build_oncmdrespext(req_id, RES_SUCCESS,
                                     make_empty_ext_pickle())
            print(f"    [>] buyItem(cd=0x{item_compact_descr:08x} "
                  f"x{count}) success price={result.get('price')}")
        else:
            msg = build_oncmdrespext(req_id, RES_FAILURE,
                                     make_empty_ext_pickle())
            print(f"    [-] buyItem(cd=0x{item_compact_descr:08x} "
                  f"x{count}) FAIL: {result.get('reason')}")
        if not send_avatar_messages(sock, addr, sess, msg, '', reliable=True):
            return
        if result.get('success') and account_id:
            try:
                push_account_diff(sock, addr, sess,
                                  build_account_state_diff(account_id))
            except Exception as exc:
                print(f"    [!] push_account_diff(buyItem) FAIL: {exc}")
        return

    if cmd == CMD_EQUIP_SHELLS:
        arr = parse_doCmd_int_arr(payload)
        account_id = int(sess.get('account_id') or 0)
        veh_inv_id = 0
        save_ok = False
        if arr:
            veh_inv_id = arr[0]
            ammo_pairs = arr[1:]
            vehicle = get_vehicle_by_inventory_id(veh_inv_id)
            if vehicle is not None:
                vehicle['defaultAmmo'] = list(ammo_pairs)
            if account_id:
                try:
                    save_vehicle_ammo_layout(account_id, veh_inv_id,
                                             ammo_pairs)
                    invalidate_sync_cache(account_id)
                    save_ok = True
                except Exception as exc:
                    print(f"    [!] save_vehicle_ammo_layout FAIL: {exc}")
        msg = build_oncmdrespext(req_id, RES_SUCCESS, make_empty_ext_pickle())
        if not send_avatar_messages(sock, addr, sess, msg, '', reliable=True):
            return
        if save_ok and account_id and veh_inv_id:
            try:
                push_account_diff(sock, addr, sess,
                                  build_vehicle_inventory_diff(account_id, veh_inv_id))
            except Exception as exc:
                print(f"    [!] push_account_diff(equipShells) FAIL: {exc}")
        print(f"    [>] equipShells(req={req_id}, items={len(arr)}) success")
        return

    if cmd == CMD_EQUIP_EQS:
        arr = parse_doCmd_int_arr(payload)
        account_id = int(sess.get('account_id') or 0)
        veh_inv_id = 0
        save_ok = False
        if arr and account_id:
            veh_inv_id = arr[0]
            slots = list(arr[1:4]) + [0, 0, 0]
            slots = slots[:3]
            try:
                save_consumable_slots(account_id, veh_inv_id, slots)
                invalidate_sync_cache(account_id)
                save_ok = True
                print(f"    [>] equipEqs(veh={veh_inv_id}, "
                      f"slots={[hex(s) for s in slots]}) saved")
            except Exception as exc:
                print(f"    [!] save_consumable_slots FAIL: {exc}")
        msg = build_oncmdrespext(req_id, RES_SUCCESS, make_empty_ext_pickle())
        if not send_avatar_messages(sock, addr, sess, msg, '', reliable=True):
            return
        if save_ok and account_id and veh_inv_id:
            try:
                push_account_diff(sock, addr, sess,
                                  build_vehicle_inventory_diff(account_id, veh_inv_id))
            except Exception as exc:
                print(f"    [!] push_account_diff(equipEqs) FAIL: {exc}")
        return

    if cmd == CMD_EQUIP_OPTDEV:
        args = parse_doCmd_int4(payload)
        account_id = int(sess.get('account_id') or 0)
        veh_inv_id = 0
        save_ok = False
        if args and account_id:
            veh_inv_id, device_cd, slot_idx, _is_paid = args
            try:
                save_optional_device_slot(account_id, veh_inv_id,
                                          slot_idx, device_cd)
                invalidate_sync_cache(account_id)
                save_ok = True
                print(f"    [>] equipOptDev(veh={veh_inv_id}, "
                      f"slot={slot_idx}, cd=0x{device_cd & 0xffffffff:08x})")
            except Exception as exc:
                print(f"    [!] save_optional_device_slot FAIL: {exc}")
        msg = build_oncmdrespext(req_id, RES_SUCCESS, make_empty_ext_pickle())
        if not send_avatar_messages(sock, addr, sess, msg, '', reliable=True):
            return
        if save_ok and account_id and veh_inv_id:
            try:
                push_account_diff(sock, addr, sess,
                                  build_vehicle_inventory_diff(account_id, veh_inv_id))
            except Exception as exc:
                print(f"    [!] push_account_diff(equipOptDev) FAIL: {exc}")
        return

    if cmd == CMD_EQUIP_TMAN:
        args = parse_doCmd_int3(payload)
        account_id = int(sess.get('account_id') or 0)
        veh_inv_id = 0
        save_ok = False
        affected_vehicles = set()
        if args and account_id:
            veh_inv_id, slot, tman_inv_id = args
            try:
                if tman_inv_id and tman_inv_id > 0:
                    _ok, prev_veh, kicked_veh = set_tankman_vehicle(
                        account_id, tman_inv_id, veh_inv_id, slot)
                    print(f"    [>] equipTman(veh={veh_inv_id}, "
                          f"slot={slot}, tman={tman_inv_id})")
                    if prev_veh:
                        affected_vehicles.add(int(prev_veh))
                    if kicked_veh:
                        affected_vehicles.add(int(kicked_veh))
                else:
                    clear_tankman_vehicle_slot(account_id, veh_inv_id, slot)
                    print(f"    [>] equipTman(veh={veh_inv_id}, "
                          f"slot={slot}, tman=NONE)")
                affected_vehicles.add(int(veh_inv_id))
                invalidate_sync_cache(account_id)
                save_ok = True
            except Exception as exc:
                print(f"    [!] set_tankman_vehicle FAIL: {exc}")
        msg = build_oncmdrespext(req_id, RES_SUCCESS, make_empty_ext_pickle())
        if not send_avatar_messages(sock, addr, sess, msg, '', reliable=True):
            return
        if save_ok and account_id:
            for affected in affected_vehicles:
                if not affected:
                    continue
                try:
                    push_account_diff(sock, addr, sess,
                                      build_crew_inventory_diff(account_id, affected))
                except Exception as exc:
                    print(f"    [!] push_account_diff(equipTman veh={affected}) FAIL: {exc}")
        return

    if cmd == CMD_DISMISS_TMAN:
        args = parse_doCmd_int3(payload)
        account_id = int(sess.get('account_id') or 0)
        ok = False
        prev_veh_inv_id = 0
        tman_inv_id = 0
        if args and account_id:
            tman_inv_id = args[0]
            try:
                ok, prev_veh_inv_id = dismiss_tankman(account_id, tman_inv_id)
                if ok:
                    invalidate_sync_cache(account_id)
                    print(f"    [>] dismissTman(tman={tman_inv_id}) ok")
                else:
                    print(f"    [-] dismissTman(tman={tman_inv_id}) "
                          f"not found")
            except Exception as exc:
                print(f"    [!] dismiss_tankman FAIL: {exc}")
        result_code = RES_SUCCESS if ok else RES_FAILURE
        msg = build_oncmdrespext(req_id, result_code, make_empty_ext_pickle())
        if not send_avatar_messages(sock, addr, sess, msg, '', reliable=True):
            return
        if ok and account_id:
            try:
                if prev_veh_inv_id:
                    push_account_diff(sock, addr, sess,
                                      build_crew_inventory_diff(
                                          account_id, prev_veh_inv_id,
                                          dismissed_tankman=tman_inv_id))
                else:
                    push_account_diff(sock, addr, sess, {
                        'inventory': {8: {
                            'compDescr': {int(tman_inv_id): None},
                            'vehicle':   {int(tman_inv_id): None},
                        }},
                    })
            except Exception as exc:
                print(f"    [!] push_account_diff(dismissTman) FAIL: {exc}")
        return

    if cmd == CMD_SYNC_DOSSIERS:
        send_dossiers_stream(sock, addr, sess, req_id)
        return

    if cmd == CMD_REQ_ARENA_LIST:
        now = time.time()
        if now - sess.get('last_arena_list_response', 0.0) < 1.0:
            return
        sess['last_arena_list_response'] = now
        send_avatar_messages(sock, addr, sess,
                             build_account_receive_active_arenas(),
                             "Account.receiveActiveArenas()",
                             reliable=True)
        send_account_server_counters(sock, addr, sess)
        return

    if cmd == CMD_REQ_QUEUE_INFO:
        randoms_length, randoms_levels, randoms_classes = get_matchmaking_queue_stats()
        send_avatar_messages(sock, addr, sess,
                             build_account_receive_queue_info(randoms_length, randoms_levels, randoms_classes),
                             "Account.receiveQueueInfo()",
                             reliable=True)
        return

    if cmd == CMD_REQ_SERVER_STATS:
        now = time.time()
        if now - sess.get('last_server_stats_response', 0.0) < 1.0:
            return
        sess['last_server_stats_response'] = now
        send_account_server_counters(sock, addr, sess,
                                     "Account.receiveServerStats()")
        return

    if cmd == CMD_ENQUEUE_FOR_ARENA:
        args = parse_doCmd_int3(payload) or (1, 0, 0)
        veh_inv_id, arena_type_id, queue_type = args
        vehicle = get_vehicle_by_inventory_id(veh_inv_id)
        if vehicle is None:
            print(f"    [!] requested battle vehicle invID={veh_inv_id} not found; "
                  f"using invID=1")
            vehicle = get_vehicle_by_inventory_id(1)
            veh_inv_id = 1
        set_session_battle_vehicle_snapshot(sess, vehicle, veh_inv_id)
        sess['battle_arena_type_id'] = normalize_arena_type_id(arena_type_id)
        sess['battle_queue_type'] = queue_type
        sess['battle_id'] = store_battle_entry(sess['battle_arena_type_id'],
                                               sess.get('account_id') or 0,
                                               veh_inv_id)
        fwd, bwd = get_vehicle_speed_limits(vehicle)
        compact = sess.get('battle_vehicle_compactDescr') or b''
        compact_hex = compact.hex() if isinstance(compact, (bytes, bytearray)) else str(compact)
        print(f"    [battle] queued invID={veh_inv_id} "
              f"name={vehicle.get('name') if vehicle else 'unknown'} "
              f"compact={compact_hex} fwd={fwd * 3.6:.1f}km/h "
              f"rev={bwd * 3.6:.1f}km/h "
              f"arenaType={sess['battle_arena_type_id']} queueType={queue_type} "
              f"battleID={sess['battle_id']}")

        # REQUEST_ID_NO_RESPONSE в†’ cmd response РЅРµ РїРѕС‚СЂС–Р±РµРЅ. РќР°С‚РѕРјС–СЃС‚СЊ С€Р»РµРјРѕ
        # entity event onEnqueued, РїС–СЃР»СЏ СЏРєРѕРіРѕ РєР»С–С”РЅС‚ РїРѕРєР°Р·СѓС” "РЈ С‡РµСЂР·С–...".
        send_account_event(sock, addr, sess,
                           ACCOUNT_ONENQUEUED_MSG_ID, "Account.onEnqueued()")
        push_vehicle_lock_diff(sock, addr, sess, LOCK_REASON_IN_QUEUE,
                               veh_inv_id)
        enqueue_for_matchmaking(sock, addr, sess)
        return

    if cmd == CMD_DEQUEUE:
        had_battle_bundle = bool(sess.get('battle_bundle_sent'))
        if had_battle_bundle:
            send_player_back_to_hangar(sock, addr, sess)
        dequeue_from_matchmaking(sess)
        with battle_lock:
            active_battle_accounts.pop(sess.get('account_id'), None)
        broadcast_account_server_counters(sock)
        send_account_event(sock, addr, sess,
                           ACCOUNT_ONDEQUEUED_MSG_ID, "Account.onDequeued()")
        push_vehicle_lock_diff(sock, addr, sess, None)
        return

    if cmd == 0 and sess.get('battle_bundle_sent'):
        msg = build_oncmdrespext(req_id, RES_SUCCESS, make_empty_ext_pickle())
        if not send_avatar_messages(sock, addr, sess, msg, '', reliable=True):
            return
        print(f"    [>] onCmdResponseExt(req={req_id}, res=0, ext=empty) "
              f"[Avatar.onClientReady]")
        send_avatar_ready_and_prebattle(sock, addr, sess)
        return

    msg = build_oncmdrespext(req_id, RES_SUCCESS, make_empty_ext_pickle())
    if not send_avatar_messages(sock, addr, sess, msg, '', reliable=True):
        return
    print(f"    [>] onCmdResponseExt(req={req_id}, res=0, ext=empty)")

def send_init_bundle(sock, addr, sess):
    if sess.get('init_sent'):
        return

    token = sess.get('token')
    if not token:
        return

    session_key_int = struct.unpack('<I', token)[0]
    init_msgs = build_init_bundle(session_key_int,
                                  sess.get('username') or 'player',
                                  sess.get('account_id') or 1)
    send_lock = sess.setdefault('send_lock', threading.RLock())
    with send_lock:
        init_pkt = build_channel_packet(init_msgs, sess, reliable=True)
        init_pkt = bw_encrypt_packet(init_pkt, sess['bf_key'])
        runtime_sendto(sock, init_pkt, addr)
    sess['init_sent'] = True
    sess['server_time_zero_wall'] = time.time()
    print(f"[>] Init bundle: authenticate+bandwidth+setGameTime"
          f"+resetEntities+createBasePlayer+showGUI(0x90)")
    send_account_server_counters(sock, addr, sess)
    broadcast_account_server_counters(sock)

    if not sess.get('tick_started'):
        start_tick_thread(sock, addr, sess)
        sess['tick_started'] = True

# в”Ђв”Ђ tickSync loop в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

def schedule_init_bundle(sock, addr, sess):
    if sess.get('init_sent') or sess.get('init_scheduled'):
        return
    sess['init_scheduled'] = True

    def _send():
        sess['init_scheduled'] = False
        send_init_bundle(sock, addr, sess)

    runtime_call_later(BASEAPP_INIT_DELAY_SECONDS, _send)


tick_counter = 0

def start_tick_thread(sock, addr, sess=None):
    """Send tickSync (MsgID 0x0D, 1 byte tick counter) every 100 ms."""
    global tick_counter
    if SERVER_RUNTIME is not None and sess is not None:
        SERVER_RUNTIME.start_session_tick(sock, addr, sess)
        return
    def _loop():
        global tick_counter
        while True:
            time.sleep(0.1)
            if sess:
                tick_byte = int(sess.get('session_tick_counter', 0)) & 0xFF
                sess['session_tick_counter'] = (tick_byte + 1) & 0xFF
            else:
                tick_byte = tick_counter & 0xFF
                tick_counter += 1
            pkt = msg_fixed(0x0D, struct.pack('<B', tick_byte))
            if sess:
                send_lock = sess.setdefault('send_lock', threading.RLock())
                with send_lock:
                    pkt = build_channel_packet(pkt, sess, reliable=False)
                    pkt = bw_encrypt_packet(pkt, sess['bf_key'])
                    try: runtime_sendto(sock, pkt, addr)
                    except: break
                continue
            else:
                pkt = make_bundle(pkt)
            try: runtime_sendto(sock, pkt, addr)
            except: break
    t = threading.Thread(target=_loop, daemon=True)
    t.start()

# в”Ђв”Ђ LoginApp handler в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

def handle_loginapp(sock):
    try:
        data, addr = sock.recvfrom(4096)
    except ConnectionResetError:
        return
    print(f"\n[+] LoginApp: {len(data)} Р±Р°Р№С‚ РІС–Рґ {addr}")
    reply_id = data[5:9]

    dec = find_rsa_block(data)
    if not dec:
        print("[-] RSA decrypt failed"); return

    username, password, bf_key = parse_logon_params(dec)
    if not username: return
    account = get_or_create_account(username, password)

    if account.get('auth_failed'):
        print(f"[-] Login REJECTED: '{username}' (invalid password)")
        err_msg = b'Invalid password.'
        resp_payload = (reply_id
                        + bytes([67])
                        + struct.pack('<I', len(err_msg))
                        + err_msg)
        resp = b'\x00\x00\xff' + struct.pack('<I', len(resp_payload)) + resp_payload
        runtime_sendto(sock, resp, addr)
        return

    token = os.urandom(4)
    active_sessions[token] = {
        'bf_key': bf_key,
        'addr': None,
        'username': account['username'],
        'account_id': account['id'],
        'known_remote_accounts': set(),
        'session_tick_counter': 0,
    }
    print(f"[+] Р›РѕРіС–РЅ: '{account['username']}' "
          f"dbid={account['id']} | Token: {token.hex()}")

    # LoginReplyRecord: Mercury::Address(ip 4B + port 2B + salt 2B) + sessionKey(4B) = 12 B
    try:
        baseapp_ip = socket.gethostbyname(PUBLIC_HOST)
    except Exception:
        baseapp_ip = '127.0.0.1'
    record  = socket.inet_aton(baseapp_ip)
    record += struct.pack('>H', BASEAPP_PORT)
    record += b'\x00\x00'            # salt (Mercury::Address.salt)
    record += token
    enc = bw_bf_encrypt(record, bf_key)

    resp_payload = reply_id + b'\x01' + enc   # status=LOGGED_ON(0x01)
    resp = b'\x00\x00\xff' + struct.pack('<I', len(resp_payload)) + resp_payload
    runtime_sendto(sock, resp, addr)
    print(f"[>] LOGGED_ON РІС–РґРїСЂР°РІР»РµРЅРѕ, РєР»С–С”РЅС‚ С–РґРµ РЅР° BaseApp:{BASEAPP_PORT}")

# в”Ђв”Ђ BaseApp handler в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

baseapp_clients = {}   # addr в†’ session info/state

def handle_baseapp(sock):
    try:
        data, addr = sock.recvfrom(4096)
    except ConnectionResetError:
        return
    if len(data) < 4: return

    # РџС–СЃР»СЏ СѓСЃРїС–С€РЅРѕРіРѕ login РєР»С–С”РЅС‚ РјРѕР¶Рµ РїРµСЂРµР№С‚Рё РЅР° Р·Р°С€РёС„СЂРѕРІР°РЅС– РїР°РєРµС‚Рё.
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

        # РџСЂРѕСЃС‚Рѕ РѕРЅРѕРІР»СЋС”РјРѕ in_seq state. ACK РїС–РіРіС–Р±РµРєР°С”РјРѕ РІ РЅР°СЃС‚СѓРїРЅРёР№
        # РІРёС…С–РґРЅРёР№ РїР°РєРµС‚ (init bundle Р°Р±Рѕ tickSync) вЂ” РѕРєСЂРµРјРёР№ ACK-РїР°РєРµС‚
        # Р·Р±РёРІР°С” РІР»Р°СЃРЅСѓ РЅСѓРјРµСЂР°С†С–СЋ out_seq.
        if sess_for_addr and (flags & PACKET_FLAG_ON_CHANNEL) and (in_seq is not None):
            update_in_seq_state(sess_for_addr, in_seq)

    msg_id = body[2]

    # в”Ђв”Ђ baseAppLogin (MsgID 0x00) в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
    if (not decrypted) and body[:2] == b'\x01\x00' and msg_id == 0x00 and len(body) >= 15:
        reply_id       = body[5:9]
        token_received = body[11:15]
        print(f"\n[+] BaseApp: baseAppLogin РІС–Рґ {addr} | Token: {token_received.hex()}")

        sess = active_sessions.get(token_received)
        if not sess:
            # РЎРїСЂРѕР±СѓС”РјРѕ Р·РЅР°Р№С‚Рё Р·Р° Р±СѓРґСЊ-СЏРєРёРј РєР»СЋС‡РµРј (РЅР° РІРёРїР°РґРѕРє СЂС–Р·РЅРёС… Р·СЃСѓРІС–РІ)
            for k, v in active_sessions.items():
                if token_received in (k, k[::-1]):
                    sess = v; break

        if not sess:
            print(f"[-] РќРµРІС–РґРѕРјРёР№ Token! Р”Р°РјРї: {data.hex()}")
            return

        sess['addr'] = addr
        sess['token'] = token_received
        # РљРѕР¶РµРЅ РЅРѕРІРёР№ baseAppLogin в†’ РЅРѕРІРёР№ channel, СЃРєРёРґР°С”РјРѕ СѓСЃС– counters,
        # С–РЅР°РєС€Рµ РїРѕРІС‚РѕСЂРЅРµ РїС–РґРєР»СЋС‡РµРЅРЅСЏ РїС–РґРµ Р· seq=N+1, Р° РєР»С–С”РЅС‚ С‡РµРєР°С” 0.
        sess['init_sent'] = False
        sess['init_scheduled'] = False
        sess['tick_started'] = False
        sess['in_seq_at'] = 0
        sess['in_seq_buffered'] = set()
        sess['in_seq_initialized'] = False
        sess['out_channel_seq'] = 0
        sess['out_nub_seq'] = 0
        sess['session_tick_counter'] = 0
        baseapp_clients[addr] = sess

        # 1) Р’С–РґРїРѕРІС–РґСЊ РЅР° baseAppLogin: РїСЂРѕСЃС‚Рѕ echo token СЏРє SessionKey
        reply = make_reply(reply_id, token_received)
        reply = bw_encrypt_packet(reply, sess['bf_key'])
        runtime_sendto(sock, reply, addr)
        schedule_init_bundle(sock, addr, sess)
        print(f"[>] baseAppLogin Reply РІС–РґРїСЂР°РІР»РµРЅРѕ")

        # 2) Init РІС–РґРїСЂР°РІРёРјРѕ РїС–СЃР»СЏ РїРµСЂС€РѕРіРѕ enableEntities/authenticate РІС–Рґ РєР»С–С”РЅС‚Р°.

    # в”Ђв”Ђ С–РЅС€С– РїРѕРІС–РґРѕРјР»РµРЅРЅСЏ РІС–Рґ РєР»С–С”РЅС‚Р° в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
    else:
        if decrypted and sess_for_addr:
            messages = list(iter_baseapp_ext_messages(body))

            for m, p, _ in messages:
                if m in (0x02, 0x03, 0x04, 0x05):
                    handle_client_avatar_update(sess_for_addr, m, p)

            # Spam filter: client sends authenticate + avatar/vehicle movement
            # updates almost every frame. Print only occasional samples.
            movement_msg_ids = (0x01, 0x02, 0x03, 0x04, 0x05)
            correction_ack_msg_ids = (0x06, 0x07)
            extra_high_rate_msg_ids = (0x0c, 0x82)
            high_rate_battle_msg_ids = movement_msg_ids + correction_ack_msg_ids + extra_high_rate_msg_ids + tuple(AVATAR_BASE_METHOD_VEHICLE_MOVE_WITH_IDS) + tuple(AVATAR_BASE_METHOD_TRACK_POINT_WITH_GUN_IDS)
            avatar_update_only = all(m in movement_msg_ids for m, _, _ in messages) \
                and any(m in (0x02, 0x03, 0x04, 0x05) for m, _, _ in messages)
            if avatar_update_only:
                cnt = sess_for_addr.get('avatar_update_count', 0) + 1
                sess_for_addr['avatar_update_count'] = cnt
                if BATTLE_VERBOSE_DEBUG and cnt % 100 == 1:
                    summary = ", ".join(f"0x{m:02x}({len(p)}B)"
                                        for m, p, _ in messages)
                    print(f"[<] movementUpdate x{cnt}: {summary}")
            elif all(m in high_rate_battle_msg_ids for m, _, _ in messages):
                cnt = sess_for_addr.get('battle_input_count', 0) + 1
                sess_for_addr['battle_input_count'] = cnt
                if BATTLE_VERBOSE_DEBUG and cnt % 100 == 1:
                    summary = ", ".join(f"0x{m:02x}({len(p)}B)"
                                        for m, p, _ in messages)
                    print(f"[<] battleInput x{cnt}: {summary}")
            else:
                summary = ", ".join(f"0x{m:02x}({len(p)}B)" for m, p, _ in messages)
                print(f"[<] BaseAppExt: {summary or 'none'} | "
                      f"flags=0x{flags:04x} seq={in_seq} body={body.hex()}")

            if any(m in (0x01, 0x0A) for m, _, _ in messages) and not sess_for_addr.get('init_sent'):
                send_init_bundle(sock, addr, sess_for_addr)
                return

            # Р‘СѓРґСЊ-СЏРєРёР№ РЅР°СЃС‚СѓРїРЅРёР№ РїР°РєРµС‚ РїС–СЃР»СЏ init вЂ“ РґР°РјРїРёРјРѕ + РІС–РґРїРѕРІС–РґР°С”РјРѕ
            # РЅР° exposed Account base-methods (doCmdStr/Int3/Int4/Int2Str/IntArr).
            deferred_avatar_shots = []
            for m, p, _ in messages:
                if m in movement_msg_ids:
                    continue
                if account_doCmd_payload_cmd(m, p) is not None:
                    handle_account_doCmd(sock, addr, sess_for_addr, m, p)
                    continue
                if m not in high_rate_battle_msg_ids:
                    print(f"    [post-init] msg=0x{m:02x} payload={p.hex()}")
                if (sess_for_addr.get('battle_bundle_sent') and
                        m in AVATAR_BASE_METHOD_VEHICLE_SHOOT_IDS and
                        len(p) == 0):
                    deferred_avatar_shots.append((m, p))
                    continue
                if sess_for_addr.get('battle_bundle_sent') and m in AVATAR_BASE_METHODS:
                    if handle_avatar_base_method(sock, addr, sess_for_addr, m, p):
                        continue
                if m in ACCOUNT_DOCMD_MSG_IDS:
                    handle_account_doCmd(sock, addr, sess_for_addr, m, p)
            for m, p in deferred_avatar_shots:
                handle_avatar_base_method(sock, addr, sess_for_addr, m, p)
            return

        print(f"[!!!] BaseApp РїР°РєРµС‚ MsgID=0x{msg_id:02x} ({len(body)}B): {body.hex()}")

# в”Ђв”Ђ Main loop в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

def main():
    import sys
    from server_core import EventLoopRuntime
    EventLoopRuntime(sys.modules[__name__]).run()

if __name__ == '__main__':
    main()

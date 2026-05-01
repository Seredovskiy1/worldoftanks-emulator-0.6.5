"""Парс list.xml + components/*.xml кожної нації, зібрати таблицю
{nationID: {vehicleTypeID: {chassis, engine, fuelTank, radio, turret, gun}}}."""
import struct, pathlib, os, json, math

MASK = 0x0fffffff
TM = 0xf0000000
# BWXML data types (high 4 bits of dataPos):
DS = 0          # DataSection (recurse)
DT_STRING = 1
DT_INT = 2
DT_FLOAT = 3
DT_BOOL = 4
DT_BLOB = 5
DT_ENC_BLOB = 6


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
    if typ != DS:
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
    own = (dps[0] & MASK) if rec else (fin & MASK)
    n['data'] = blob[do:do + own]
    for i, (dp, k) in enumerate(rec):
        st = dp & MASK; en = dps[i + 1] & MASK
        ct = (dps[i + 1] & TM) >> 28          # розпакований type ID
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


def find_all(n, name):
    return [c for c in n['children'] if c['name'] == name]


def find_path(n, path):
    cur = n
    for part in path.split('/'):
        cur = find(cur, part) if cur is not None else None
    return cur


def load(fp):
    b = pathlib.Path(fp).read_bytes()
    if not b:
        return None
    s, o = ps(b, 5)
    return parse(b[o:], DS, 'r', s)


def get_text(n):
    if n is None:
        return None
    return n['data'].decode('latin1').strip() if n['data'] else ''


def get_int(n, default=0):
    """Розпарсити value БУДЬ-ЯКОГО BWXML node як integer.
    Тип node визначає інтерпретацію data:
      DT_INT  → binary little-endian (1/2/4/8 байт)
      DT_STRING → ASCII-стрічка ("5" → 5)
    Раніше get_int плутав binary 0x35 (=53) з ASCII '5' (=5) — bug.
    """
    if n is None:
        return default
    d = n['data']
    if not d:
        return default
    t = n.get('type', DT_INT)
    if t == DT_INT:
        return int.from_bytes(d, 'little', signed=True)
    if t == DT_STRING:
        try:
            return int(float(d.decode('latin1').strip()))
        except Exception:
            return default
    # Fallback — старе поводження для unknown типу
    if len(d) <= 8:
        return int.from_bytes(d, 'little', signed=True)
    return default


NATIONS = ['ussr', 'germany', 'usa']  # 0.6.5 has these
NATION_ID = {n: i for i, n in enumerate(NATIONS)}

base = r'C:\Users\qwerty\Desktop\World_of_Tanks\res\scripts\item_defs\vehicles'


def parse_components_xml(path):
    """Структура: <ids><Name1>id1</Name1><Name2>id2</Name2>...</ids>+<shared>...</shared>.
    Повертає {Name: id} (id — це безпосередньо текстове число у вузлі)."""
    if not os.path.exists(path):
        return {}
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


def first_component(comps):
    """Перший НЕНУЛЬОВИЙ id у dict (у XML-порядку).
    BWXML нерідко має placeholder-елементи з порожнім data (id=0) —
    g_cache не містить таких, тож використання id=0 викликає KeyError на
    клієнті. Беремо перший компонент із id > 0."""
    for v in comps.values():
        if v > 0:
            return v
    return 0


def parse_turrets(path):
    """turrets.xml: <ids><Name1>id1</Name1>...</ids>+<shared>...</shared>.
    Повертає просто dict (як інші компоненти) — gun-mounting детальніше
    нам не потрібно (client _descrByID лише перевіряє існування ID)."""
    return parse_components_xml(path)


def parse_list_xml(nation):
    """list.xml — кожен танк <NameTank>: <id>N</id>."""
    p = os.path.join(base, nation, 'list.xml')
    n = load(p)
    if n is None:
        return []
    vehicles = []
    for c in n['children']:
        if c['name'] in ('xmlns:xmlref', 'shared'):
            continue
        idn = find(c, 'id')
        if idn is None:
            continue
        vehicles.append((c['name'], get_int(idn)))
    return vehicles


def make_compact_descr(nation_id, vtype_id, chassis, engine, fuel, radio, turret, gun):
    header = 1 | (nation_id << 4)  # ITEM_TYPE_INDICES['vehicle']=1
    return struct.pack('<2B6H2B',
                       header, vtype_id,
                       chassis, engine, fuel, radio, turret, gun,
                       0, 0)


def first_child_name(node, section_name):
    """Повертає ім'я першого дочірнього вузла секції (chassis/engines/...)
    у vehicle XML. Це ім'я використовується як ключ у components/*.xml."""
    sec = find(node, section_name)
    if sec is None or not sec['children']:
        return None
    return sec['children'][0]['name']


def first_gun_in_turret(node, turret_name):
    """У vehicle XML <turrets0><turret_name><guns><gun_name>...</guns></turret>.
    Повертає назву першої гармати у вказаній башті."""
    turrets0 = find(node, 'turrets0')
    if turrets0 is None:
        return None
    turret = find(turrets0, turret_name)
    if turret is None:
        return None
    guns = find(turret, 'guns')
    if guns is None or not guns['children']:
        return None
    return guns['children'][0]['name']


def get_float(n, default=0.0):
    if n is None:
        return default
    d = n['data']
    if not d:
        return default
    t = n.get('type', DT_FLOAT)
    if t == DT_FLOAT and len(d) == 4:
        return struct.unpack('<f', d)[0]
    if t == DT_INT:
        return float(get_int(n, int(default)))
    if t == DT_STRING:
        try:
            return float(d.decode('latin1').strip())
        except Exception:
            return default
    return default


def get_price(n):
    text = get_text(n)
    if not text:
        return (0, 0)
    parts = text.replace(',', ' ').split()
    try:
        credits = int(float(parts[0])) if parts else 0
        gold = int(float(parts[1])) if len(parts) > 1 else 0
        return (credits, gold)
    except Exception:
        return (0, 0)


def make_int_compact_descr(item_type_id, nation_id, comp_type_id):
    return (comp_type_id << 8) + (nation_id << 4) + item_type_id


def parse_shells_xml(path, nation_id):
    n = load(path)
    if n is None:
        return {}
    out = {}
    for c in n['children']:
        if c['name'] == 'icons':
            continue
        shell_id = get_int(find(c, 'id'))
        out[c['name']] = {
            'id': shell_id,
            'compactDescr': make_int_compact_descr(10, nation_id, shell_id),
            'price': get_price(find(c, 'price')),
        }
    return out


def parse_gun_shots(path, gun_name, nation_id, shells_map):
    n = load(path)
    if n is None:
        return []
    shared = find(n, 'shared')
    gun = find(shared, gun_name) if shared is not None else None
    shots = find(gun, 'shots') if gun is not None else None
    if shots is None:
        return []
    out = []
    for shot in shots['children']:
        shell = shells_map.get(shot['name'])
        if shell is None:
            continue
        out.append({
            'name': shot['name'],
            'compactDescr': shell['compactDescr'],
            'defaultPortion': get_float(find(shot, 'defaultPortion'), 0.0),
            'price': shell['price'],
            'speed': get_float(find(shot, 'speed'), 800.0),
            'gravity': get_float(find(shot, 'gravity'), 9.81),
            'effectsIndex': get_int(find(shot, 'effectsIndex'), 0),
        })
    return out


def get_shared_component_node(path, name):
    n = load(path)
    shared = find(n, 'shared') if n is not None else None
    return find(shared, name) if shared is not None and name else None


def get_vehicle_speed_limits(veh_node):
    forward = get_float(find_path(veh_node, 'speedLimits/forward'), 36.0) / 3.6
    backward = get_float(find_path(veh_node, 'speedLimits/backward'), 14.0) / 3.6
    return [forward, backward]


def make_default_ammo(shots, max_ammo):
    ammo = []
    current = 0
    for shot in shots:
        count = int(shot['defaultPortion'] * max_ammo + 0.5)
        if current + count > max_ammo:
            count = max_ammo - current
        current += count
        ammo.extend([shot['compactDescr'], count])
    if shots and current < max_ammo:
        ammo[1] += max_ammo - current
    return ammo


def get_gun_max_ammo(veh_node, turret_name, gun_name, gun_node=None):
    turrets0 = find(veh_node, 'turrets0')
    turret = find(turrets0, turret_name) if turrets0 is not None else None
    guns = find(turret, 'guns') if turret is not None else None
    gun = find(guns, gun_name) if guns is not None else None
    value = get_int(find(gun, 'maxAmmo'), 0) if gun is not None else 0
    if value <= 0 and gun_node is not None:
        value = get_int(find(gun_node, 'maxAmmo'), 0)
    return value


def get_vehicle_max_health(veh_node, turret_node, turret_name):
    value = get_int(find_path(veh_node, 'hull/maxHealth'), 0)
    turrets0 = find(veh_node, 'turrets0')
    turret = find(turrets0, turret_name) if turrets0 is not None else None
    turret_value = get_int(find(turret, 'maxHealth'), 0) if turret is not None else 0
    if turret_value <= 0:
        turret_value = get_int(find(turret_node, 'maxHealth'), 0)
    if value <= 0:
        value = get_int(find(veh_node, 'maxHealth'), 1)
    return value + turret_value


result = {'vehicles': []}

for nation in NATIONS:
    nid = NATION_ID[nation]
    nat_dir = os.path.join(base, nation)
    comp_dir = os.path.join(nat_dir, 'components')

    chassis_map = parse_components_xml(os.path.join(comp_dir, 'chassis.xml'))
    engines_map = parse_components_xml(os.path.join(comp_dir, 'engines.xml'))
    fuel_map = parse_components_xml(os.path.join(comp_dir, 'fuelTanks.xml'))
    radio_map = parse_components_xml(os.path.join(comp_dir, 'radios.xml'))
    turret_map = parse_components_xml(os.path.join(comp_dir, 'turrets.xml'))
    gun_map = parse_components_xml(os.path.join(comp_dir, 'guns.xml'))
    shells_map = parse_shells_xml(os.path.join(comp_dir, 'shells.xml'), nid)

    print(f"\n=== {nation} (nationID={nid}) ===")

    skipped = 0
    for veh_name, vtype_id in parse_list_xml(nation):
        veh_xml_path = os.path.join(nat_dir, veh_name + '.xml')
        veh = load(veh_xml_path)
        if veh is None:
            print(f"  [SKIP] {veh_name}: XML not found")
            skipped += 1
            continue

        # Беремо ім'я першого дочірнього вузла з кожної секції
        ch_name = first_child_name(veh, 'chassis')
        en_name = first_child_name(veh, 'engines')
        fl_name = first_child_name(veh, 'fuelTanks')
        rd_name = first_child_name(veh, 'radios')
        tr_name = first_child_name(veh, 'turrets0')
        gn_name = first_gun_in_turret(veh, tr_name) if tr_name else None

        # Lookup name → id з components/*.xml
        chassis_id = chassis_map.get(ch_name)
        engine_id = engines_map.get(en_name)
        fuel_id = fuel_map.get(fl_name)
        radio_id = radio_map.get(rd_name)
        turret_id = turret_map.get(tr_name)
        gun_id = gun_map.get(gn_name)
        chassis_node = get_shared_component_node(os.path.join(comp_dir, 'chassis.xml'), ch_name)
        turret_node = get_shared_component_node(os.path.join(comp_dir, 'turrets.xml'), tr_name)
        gun_node = get_shared_component_node(os.path.join(comp_dir, 'guns.xml'), gn_name)
        gun_shots = parse_gun_shots(os.path.join(comp_dir, 'guns.xml'),
                                    gn_name, nid, shells_map) if gn_name else []
        max_ammo = get_gun_max_ammo(veh, tr_name, gn_name, gun_node) if tr_name and gn_name else 0
        max_health = get_vehicle_max_health(veh, turret_node, tr_name)
        speed_limits = get_vehicle_speed_limits(veh)
        chassis_rotation_speed = math.radians(get_float(find(chassis_node, 'rotationSpeed'), 30.0))
        turret_rotation_speed = math.radians(get_float(find(turret_node, 'rotationSpeed'), 30.0))
        gun_rotation_speed = math.radians(get_float(find(gun_node, 'rotationSpeed'), 30.0))
        reload_time = get_float(find(gun_node, 'reloadTime'), 5.0)

        if None in (chassis_id, engine_id, fuel_id, radio_id, turret_id, gun_id):
            print(f"  [SKIP] {veh_name}: missing comp "
                  f"ch={ch_name}={chassis_id} en={en_name}={engine_id} "
                  f"fl={fl_name}={fuel_id} rd={rd_name}={radio_id} "
                  f"tr={tr_name}={turret_id} gn={gn_name}={gun_id}")
            skipped += 1
            continue

        # crew section: <crew><commander/><driver/>...</crew> — кількість
        # ролей == розмір екіпажу. Hangar.isCrewFull() = True лише якщо
        # vehicle.crew має точно стільки ж елементів і всі != None.
        crew_node = find(veh, 'crew')
        crew_size = len(crew_node['children']) if crew_node else 4

        cd = make_compact_descr(nid, vtype_id, chassis_id, engine_id,
                                fuel_id, radio_id, turret_id, gun_id)
        print(f"  [{vtype_id:3d}] {veh_name:25s} ch={chassis_id:3d} en={engine_id:3d} "
              f"fl={fuel_id:3d} rd={radio_id:3d} tr={turret_id:3d} gn={gun_id:3d} crew={crew_size}")
        result['vehicles'].append({
            'nation': nation, 'nationID': nid,
            'name': veh_name, 'vehicleTypeID': vtype_id,
            'compactDescr_hex': cd.hex(),
            'crewSize': crew_size,
            'turretCompactDescr': make_int_compact_descr(3, nid, turret_id),
            'gunCompactDescr': make_int_compact_descr(4, nid, gun_id),
            'maxHealth': max_health,
            'speedLimits': speed_limits,
            'chassisRotationSpeed': chassis_rotation_speed,
            'turretRotationSpeed': turret_rotation_speed,
            'gunRotationSpeed': gun_rotation_speed,
            'reloadTime': reload_time,
            'maxAmmo': max_ammo,
            'defaultAmmo': make_default_ammo(gun_shots, max_ammo),
            'shells': gun_shots,
        })

    if skipped:
        print(f"  [{nation}] skipped {skipped} vehicles")

with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       '_vehicles.json'), 'w') as f:
    json.dump(result, f, indent=2)
print(f"\nTotal vehicles: {len(result['vehicles'])}")
print("Saved -> _vehicles.json")

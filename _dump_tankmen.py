"""Парс tankmen/<nation>.xml → _tankmen.json з first/last names + icons.

Формат tankman compact descriptor (з res/scripts/common/items/tankmen.py):
  header(1) | vehicleTypeID(1) | roleID(1) | roleLevel(1)
  numSkills(1) | skills... | lastSkillLevel(1)
  flags(1) | firstNameID(2) | lastNameID(2) | iconID(2) | rank(1) | levels(1) | freeXP(4)
  + dossier(0..)

Кожен ID мусить існувати у nationConfig['firstNames'/'lastNames'/'icons'],
інакше client TankmanDescr.__initFromCompactDescr впаде з KeyError.
"""
import struct, pathlib, os, json

MASK = 0x0fffffff
TM = 0xf0000000
DS = 0
DT_STRING = 1
DT_INT = 2


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
        ct = (dps[i + 1] & TM) >> 28
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


def get_text(n):
    if n is None:
        return None
    return n['data'].decode('utf-8', errors='replace').strip() if n['data'] else ''


def load(fp):
    b = pathlib.Path(fp).read_bytes()
    if not b:
        return None
    s, o = ps(b, 5)
    return parse(b[o:], DS, 'r', s)


def parse_id_section(node):
    res = []
    if node is None:
        return res
    for child in node['children']:
        name = child['name']
        if not name.startswith('_'):
            continue
        try:
            idn = int(name[1:])
        except Exception:
            continue
        text = get_text(child) or ''
        res.append((idn, text))
    return res


def parse_groups(root, kind):
    out = []
    section = find(root, kind)
    if section is None:
        return out
    for child in section['children']:
        sex_node = find(child, 'sex')
        sex = (get_text(sex_node) or 'male').lower()
        weight_node = find(child, 'weight')
        try:
            weight = float(get_text(weight_node) or '1.0')
        except Exception:
            weight = 1.0
        first_names = parse_id_section(find(child, 'firstNames'))
        last_names = parse_id_section(find(child, 'lastNames'))
        icons = parse_id_section(find(child, 'icons'))
        out.append({
            'isFemale': (sex == 'female'),
            'weight': weight,
            'firstNames': first_names,
            'lastNames': last_names,
            'icons': icons,
        })
    return out


NATIONS = ['ussr', 'germany', 'usa']
NATION_ID = {n: i for i, n in enumerate(NATIONS)}
BASE = r'C:\Users\qwerty\Desktop\World_of_Tanks\res\scripts\item_defs\tankmen'


def main():
    out = {}
    for nation in NATIONS:
        path = os.path.join(BASE, nation + '.xml')
        if not os.path.exists(path):
            print(f'[!] missing {path}')
            continue
        root = load(path)
        normal = parse_groups(root, 'normalGroups')
        premium = parse_groups(root, 'premiumGroups')
        all_first = sum((g['firstNames'] for g in normal + premium), [])
        all_last  = sum((g['lastNames']  for g in normal + premium), [])
        all_icon  = sum((g['icons']      for g in normal + premium), [])
        out[nation] = {
            'id': NATION_ID[nation],
            'normalGroups': normal,
            'premiumGroups': premium,
        }
        print(f'[*] {nation}: '
              f'normalGroups={len(normal)} premiumGroups={len(premium)} '
              f'firstNames={len(all_first)} lastNames={len(all_last)} '
              f'icons={len(all_icon)}')
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           '_tankmen.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print('[*] _tankmen.json written')


if __name__ == '__main__':
    main()

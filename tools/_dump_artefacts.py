"""Парс equipments.xml + optional_devices.xml → _artefacts.json."""
import struct, pathlib, os, json

MASK = 0x0fffffff
TM = 0xf0000000
DS = 0


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
    if n is None or not n['data']:
        return ''
    return n['data'].decode('utf-8', errors='replace').strip()


def get_int(n, default=0):
    if n is None or not n['data']:
        return default
    d = n['data']
    if len(d) <= 8:
        try:
            return int.from_bytes(d, 'little', signed=True)
        except Exception:
            pass
    try:
        return int(float(d.decode('latin1').strip()))
    except Exception:
        return default


def load(fp):
    b = pathlib.Path(fp).read_bytes()
    if not b:
        return None
    s, o = ps(b, 5)
    return parse(b[o:], DS, 'r', s)


def parse_artefacts(root):
    if root is None:
        return []
    items = []
    for child in root['children']:
        if child['name'] in ('xmlns:xmlref', 'shared'):
            continue
        idn = find(child, 'id')
        if idn is None:
            continue
        item_id = get_int(idn)
        if item_id <= 0:
            continue
        price_node = find(child, 'price')
        price = (0, 0)
        if price_node is not None:
            text = get_text(price_node)
            try:
                parts = [int(float(p)) for p in text.split() if p.strip()]
                if len(parts) >= 2:
                    price = (parts[0], parts[1])
                elif parts:
                    price = (parts[0], 0)
            except Exception:
                pass
        gold_node = find(price_node, 'gold') if price_node is not None else None
        if gold_node is not None:
            text = get_text(gold_node).lower()
            if text in ('true', '1'):
                price = (0, price[0])
        tags_node = find(child, 'tags')
        tags = (get_text(tags_node) or '').split()
        items.append({
            'name': child['name'],
            'id': item_id,
            'price': list(price),
            'tags': tags,
        })
    return items


ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
DATA_DIR = os.path.join(ROOT_DIR, 'data')
BASE = r'C:\Users\qwerty\Desktop\World_of_Tanks\res\scripts\item_defs\vehicles\common'


def main():
    out = {}
    for kind, fname in (('equipments', 'equipments.xml'),
                        ('optional_devices', 'optional_devices.xml')):
        path = os.path.join(BASE, fname)
        if not os.path.exists(path):
            print(f'[!] missing {path}')
            out[kind] = []
            continue
        root = load(path)
        items = parse_artefacts(root)
        out[kind] = items
        print(f'[*] {kind}: {len(items)} items')
        for it in items[:5]:
            print('    ' + repr(it).encode('ascii', 'replace').decode('ascii'))
    os.makedirs(DATA_DIR, exist_ok=True)
    target = os.path.join(DATA_DIR, '_artefacts.json')
    with open(target, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f'[*] wrote {target}')


if __name__ == '__main__':
    main()

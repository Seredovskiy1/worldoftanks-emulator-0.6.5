"""BigWorld PackedSection (.def) parser. Витягує порядок ClientMethods Account."""
import struct, sys, os

DEF_PATH = sys.argv[1] if len(sys.argv) > 1 else r"World_of_Tanks\res\scripts\entity_defs\Account.def"
data = open(DEF_PATH, 'rb').read()
print(f"file: {DEF_PATH}  size={len(data)}B")

magic, = struct.unpack('<I', data[:4])
assert magic == 0x62a14e45
version = data[4]
print(f"magic=0x{magic:08x} version={version}")

# String table
off = 5
strings = []
while True:
    end = data.index(b'\0', off)
    s = data[off:end].decode('utf-8', errors='replace')
    off = end + 1
    if s == '':
        break
    strings.append(s)
print(f"string-table: {len(strings)} entries")

ROOT_OFF = off

DATA_POS_MASK = 0x0FFFFFFF
TYPE_SHIFT = 28

TYPE_NAMES = {0: 'SECTION', 1: 'STRING', 2: 'INT', 3: 'FLOAT',
              4: 'BOOL', 5: 'BLOB', 6: 'ENC_BLOB'}


def parse_section(sec_off):
    """Returns dict: name->? Actually returns list of children records."""
    num_children = struct.unpack_from('<h', data, sec_off)[0]
    rec_off = sec_off + 2
    # records: N entries of 6 bytes (dataPos+keyPos), then final 4 bytes (dataPos)
    records = []
    for i in range(num_children):
        dp_raw, kp = struct.unpack_from('<iH', data, rec_off + i * 6)
        records.append((dp_raw, kp))
    final_dp_raw = struct.unpack_from('<i', data, rec_off + num_children * 6)[0]

    own_dp_raw = records[0][0] if num_children > 0 else final_dp_raw
    own_endpos = own_dp_raw & DATA_POS_MASK
    own_type = (own_dp_raw >> TYPE_SHIFT) & 0xF

    # Block data starts after header
    block_data_start = rec_off + num_children * 6 + 4

    children = []
    prev_end = own_endpos  # own data first, then children
    for i in range(num_children):
        # endPos for child[i] is in record[i+1].dataPos OR final_dp if i == N-1
        if i + 1 < num_children:
            ep_raw = records[i + 1][0]
        else:
            ep_raw = final_dp_raw
        endpos = ep_raw & DATA_POS_MASK
        ctype = (ep_raw >> TYPE_SHIFT) & 0xF
        keypos = records[i][1]
        name = strings[keypos] if keypos < len(strings) else f"<bad k{keypos}>"
        child_data_off = block_data_start + prev_end
        child_data_size = endpos - prev_end
        children.append({
            'name': name, 'type': ctype,
            'data_off': child_data_off, 'data_size': child_data_size,
        })
        prev_end = endpos
    return children, own_type, own_endpos, block_data_start


def walk(off, depth=0, name='<root>', parent_path=''):
    children, own_type, own_size, block_start = parse_section(off)
    indent = '  ' * depth
    full = parent_path + '/' + name
    show = depth <= 1 or 'Method' in name or 'Method' in parent_path
    if show:
        print(f"{indent}[{TYPE_NAMES.get(own_type, own_type)}] {name}  "
              f"({len(children)} children, ownData={own_size}B)")
    for ch in children:
        if ch['type'] == 0 and ch['data_size'] >= 2:  # SECTION
            walk(ch['data_off'], depth + 1, ch['name'], full)
        elif show:
            print(f"{indent}  - {ch['name']}  [{TYPE_NAMES.get(ch['type'], ch['type'])}, {ch['data_size']}B]")


print("\n=== Walking root ===")
walk(ROOT_OFF, 0, os.path.basename(DEF_PATH))

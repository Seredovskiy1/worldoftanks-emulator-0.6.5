import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import emulator

space_dir = emulator.find_client_space_dir(1)
print(f"space_dir={space_dir}")

rejected = []
accepted_count = 0
for name in os.listdir(space_dir):
    if not name.endswith('.chunk') or len(name) < 14:
        continue
    stem = os.path.splitext(name)[0]
    if len(stem) < 9 or not stem.endswith('o'):
        continue
    try:
        chunk_x = emulator.signed_chunk_coord(stem[:4])
        chunk_z = emulator.signed_chunk_coord(stem[4:8])
        with open(os.path.join(space_dir, name), 'rb') as fh:
            data = fh.read()
    except (OSError, ValueError):
        continue
    for instance in emulator.iter_static_model_instances_from_chunk(data):
        model_path = instance['model']
        transform = instance['transform']
        local_x = transform['local'][0]
        local_y = transform['local'][1]
        local_z = transform['local'][2]
        x = chunk_x * emulator.BATTLE_TERRAIN_CHUNK_SIZE + local_x
        z = chunk_z * emulator.BATTLE_TERRAIN_CHUNK_SIZE + local_z
        terrain_y = emulator.terrain_height_only(1, x, z, local_y)
        diff = abs(local_y - terrain_y)
        if diff > emulator.STATIC_OBSTACLE_TERRAIN_Y_TOLERANCE:
            rejected.append((diff, x, local_y, terrain_y, model_path.decode('latin-1', 'replace')))
        else:
            accepted_count += 1

rejected.sort(key=lambda r: -r[0])
print(f"accepted={accepted_count} rejected={len(rejected)} tolerance={emulator.STATIC_OBSTACLE_TERRAIN_Y_TOLERANCE}")
for diff, x, local_y, terrain_y, model in rejected:
    print(f"  diff={diff:.2f}m x={x:.1f} local_y={local_y:.2f} terrain_y={terrain_y:.2f}  {model}")

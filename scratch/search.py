def find_in_file(path, pattern):
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for idx, line in enumerate(f):
            if pattern in line:
                print(f"{path}:{idx+1}: {line.strip()}")

find_in_file(r"c:\Users\qwerty\Documents\GitHub\worldoftanks-emulator-0.6.5\server_core\emulator_impl.py", "battle_motion_force_position")

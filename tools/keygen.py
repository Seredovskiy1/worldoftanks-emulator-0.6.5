from Crypto.PublicKey import RSA
import os

# Шлях до файлу в папці гри (взято з твого ProcMon)
game_key_path = r"C:\Users\qwerty\Desktop\World_of_Tanks\res\loginapp_wot.pubkey"

print("[*] Генеруємо нові чисті ключі...")

# Генеруємо RSA-2048 ключ
key = RSA.generate(2048)

# Зберігаємо приватний ключ для нашого сервера
with open("private_key.pem", "wb") as f:
    f.write(key.export_key())
print("[+] Приватний ключ збережено у private_key.pem")

# Записуємо публічний ключ прямо в гру
try:
    with open(game_key_path, "wb") as f:
        f.write(key.publickey().export_key(format='PEM'))
    print(f"[+ SUCCESS] Ключ успішно вшито в гру: {game_key_path}")
except Exception as e:
    print(f"[! ERROR] Не вдалося записати файл: {e}")

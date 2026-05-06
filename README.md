# worldoftanks-emulator-0.6.5

Лоу-левел емулятор серверу World of Tanks 0.6.5 (LoginApp + BaseApp).

## Залежності

```powershell
pip install -r requirements.txt
```

## База даних — MySQL / MariaDB

Сервер тримає всі персистентні дані (акаунти, валюта, бої, досьє) у MySQL,
тому ти можеш переглядати/редагувати їх через **phpMyAdmin**.

### 1. Запусти MySQL

Найшвидше через **XAMPP** / **Laragon** / **MariaDB**. За замовчуванням
сервер очікує:

| Параметр   | Значення   |
| ---------- | ---------- |
| host       | `127.0.0.1` |
| port       | `3306`      |
| user       | `root`      |
| password   | *(порожній)* |
| database   | `wot_emulator` |

Перевизначається через змінні середовища:
`MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DB`.

### 2. Створи БД та таблиці

Варіант A — **автоматично**: просто запусти `python emulator.py`. При першому
старті сервер створить базу `wot_emulator` і всі таблиці сам.

Варіант B — **вручну через phpMyAdmin**: відкрий `http://localhost/phpmyadmin`,
натисни *Import* і завантаж файл `schema.sql` із цього репозиторію.

### 3. Запуск сервера

```powershell
python emulator.py
```

При старті в консолі ти маєш побачити:

```
[*] MySQL: schema ready on 127.0.0.1:3306/wot_emulator
```

### Що зберігається

- **`accounts`** — `id`, `username`, `password_hash`, `credits`, `gold`,
  `free_xp`, `slots`, `berths`, `premium_expire_at`, `attrs`, `clan_db_id`.
- **`account_unlocks`**, **`account_elite_vehicles`**,
  **`account_double_xp_vehicles`** — set-и розблокованих/елітних танків.
- **`battles`** — `arena_type_id`, `created_at`, `finished_at`, `winner_team`.
- **`battle_entries`** — хто з якого тенка зайшов у бій.
- **`battle_results`** — підсумки бою на акаунт: `frags`, `damage_dealt`,
  `credits_earned`, `xp_earned`, тощо. Записується по завершенні бою.
- **`dossier`** — агрегована статистика акаунта (всього боїв, перемог,
  рекорди, разом XP/кредитів).

Після кожного бою сервер автоматично:

1. Вставляє рядок у `battle_results`.
2. Збільшує `accounts.credits` та `accounts.free_xp` на зароблену суму.
3. Інкрементить лічильники в `dossier`.
4. Інвалідить кеш syncData → клієнт побачить актуальний баланс.

Якщо в phpMyAdmin відредагуєш `accounts.credits` / `gold` / `free_xp`,
наступний логін гравця підтягне нові значення.
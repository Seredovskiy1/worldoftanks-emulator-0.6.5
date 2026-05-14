# Hosting WoT 0.6.5 Emulator for Remote Players

## Server config files

The emulator reads JSON config from `config/*.example.json`, then `config/*.local.json`, then environment variables. Keep real secrets and machine-specific values in local files, for example `config/server.local.json`:

```json
{
  "server": {
    "public_host": "123.45.67.89"
  },
  "database": {
    "password": "your_mysql_password"
  }
}
```

You can also configure battle timers, matchmaking, enabled maps, spawns, capture bases, movement, shot radius, penetration, and artillery settings through the JSON files in `config/`.

Інструкція для адміна сервера: як підняти емулятор так, щоб люди могли підключатись з інтернету.

## Що потрібно

1. Машина (VPS, домашній сервер, ваш комп) з публічним IP або DDNS.
2. Відкриті UDP порти: **20016** (LoginApp) і **20017** (BaseApp).
3. MySQL/MariaDB сервер (локально на тій самій машині).
4. Python 3.9+.

## 1. Запустити емулятор з правильним публічним хостом

Емулятор тепер читає `WOT_PUBLIC_HOST` з оточення і повертає цю адресу клієнтам у LoginReply.

### Windows (PowerShell)

```powershell
$env:WOT_PUBLIC_HOST = "123.45.67.89"   # твій публічний IP або домен
python emulator.py
```

### Linux

```bash
WOT_PUBLIC_HOST=123.45.67.89 python3 emulator.py
```

При старті ти маєш побачити:
```
[*] WoT 0.6.5 Emulator | LoginApp:20016 | BaseApp:20017
[*] PUBLIC_HOST=123.45.67.89 (advertised to clients in LoginReply)
```

Якщо бачиш `[!] PUBLIC_HOST=127.0.0.1 — only local clients can connect.` — то env-змінна не задана, віддалені клієнти не зможуть грати.

## 2. Прокинути порти

### Домашній роутер (NAT)

Зайди в адмінку роутера → **Port Forwarding** → додай:
- **UDP 20016** → внутрішня IP машини, порт 20016
- **UDP 20017** → внутрішня IP машини, порт 20017

### Windows Firewall

```powershell
New-NetFirewallRule -DisplayName "WoT Emu LoginApp" -Direction Inbound -Protocol UDP -LocalPort 20016 -Action Allow
New-NetFirewallRule -DisplayName "WoT Emu BaseApp" -Direction Inbound -Protocol UDP -LocalPort 20017 -Action Allow
```

### Linux ufw

```bash
sudo ufw allow 20016/udp
sudo ufw allow 20017/udp
```

## 3. Перевірка з зовнішньої машини

З іншого комп'ютера в інтернеті:

```powershell
Test-NetConnection -ComputerName 123.45.67.89 -Port 20016 -InformationLevel Detailed
```

Якщо `TcpTestSucceeded` False (бо UDP), то це норма — Test-NetConnection тестує TCP. Просто переконайся що нічого не блокує.

Краще перевіряти через клієнт: гравець ставить hosts entry і логіниться. У серверному логу маєш побачити `[+] LoginApp: ... байт від (<публічний IP гравця>, ...)`.

## 4. Зібрати дистрибутив для гравців

Гравці отримують **тільки клієнт + лаунчер** (без емулятора, MySQL, Python, тощо).

### Що покласти в архів

```
World_of_Tanks_PrivateServer.zip
└── World_of_Tanks/         <- повна папка гри (WorldOfTanks.exe всередині)
    ├── WorldOfTanks.exe
    ├── res/
    ├── ... (звичайні файли гри)
    ├── setup.bat           <- з папки launcher/
    ├── play.bat            <- з папки launcher/
    ├── uninstall.bat       <- з папки launcher/
    └── README.md           <- з папки launcher/ (інструкція для гравця)
```

### Автоматично (PowerShell)

```powershell
.\build_player_distribution.ps1 -GamePath "C:\Path\To\World_of_Tanks" -ServerHost "123.45.67.89"
```

Скрипт зробить усе: скопіює клієнт, додасть лаунчер, підставить ваш `SERVER_HOST`, запакує у `WoT_PrivateServer.zip`. Готовий архів роздавай гравцям.

### Або вручну

1. Скопіюй папку гри `World_of_Tanks/` (вашу робочу версію клієнта).
2. Скопіюй з `launcher/` у корінь `World_of_Tanks/` файли: `setup.bat`, `play.bat`, `uninstall.bat`, `README.md`.
3. Відредагуй `setup.bat` і `uninstall.bat`: знайди рядок
   ```
   set "SERVER_HOST=YOUR_SERVER_IP_OR_DOMAIN"
   ```
   і заміни на свій публічний IP/домен (той самий що задано у `WOT_PUBLIC_HOST` на сервері).
4. Заархівуй папку `World_of_Tanks/` цілком у `.zip` або `.7z`.
5. Роздай гравцям.

### Що НЕ слід давати гравцям

- ❌ `emulator.py`, `schema.sql`, `private_key.pem`, `requirements.txt`
- ❌ MySQL, Python
- ❌ `HOSTING.md` (це для тебе)
- ❌ Сам код емулятора

Гравці повинні мати **тільки клієнт + 4 файли лаунчера**.

## 5. Альтернатива: DDNS

Якщо твій IP динамічний (домашній інтернет), використай DDNS:
- DuckDNS, No-IP, Dynu — безкоштовні
- Реєструй щось типу `mywotemu.duckdns.org`
- Встановлюєш DDNS-клієнт на сервер
- В `setup.bat` і `WOT_PUBLIC_HOST` ставиш `mywotemu.duckdns.org`

При зміні IP домен оновиться автоматично, гравцям нічого міняти не треба.

## 6. Безпека

- Не виставляй MySQL назовні (3306). Хай слухає лише `127.0.0.1`.
- Емулятор не має captcha/rate-limit на LoginApp — будь готовий до спаму. Можна додати IP whitelist на firewall рівні якщо роздаєш малому колу.
- Якщо емулятор крешнеться — гравці отримають дисконнект; перезапуск автоматично через `nssm` (Win) або `systemd` (Linux) рекомендовано для стабільності.

## Часті проблеми

### Гравець бачить "Connection lost" після логіну

→ Не прокинуто UDP **20017** (BaseApp). Перевір port forwarding.

### Гравець не може залогінитись зовсім

→ Не прокинуто UDP **20016** (LoginApp), або hosts не налаштовано (запусти `setup.bat`).

### У серверному логу `LoginApp: ... байт від (192.168.X.X, ...)`, але клієнт не отримує відповідь

→ Дивись `[*] PUBLIC_HOST=...` у старті. Якщо там 127.0.0.1 — треба `WOT_PUBLIC_HOST=<your_ip>`.

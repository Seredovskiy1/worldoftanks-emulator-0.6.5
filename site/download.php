<?php
require_once 'db.php';
?>
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>World of Tanks Project Orion 0.6.5 - Скачать игру</title>
    <link rel="stylesheet" href="style.css">
    <link rel="icon" type="image/png" href="favicon.png">
    <script src="https://cdn.tailwindcss.com"></script>
    <script>tailwind.config={important:true,theme:{extend:{colors:{wot:{gold:'#e5a93b',dark:'#1a1a1c',panel:'#101011'}}}}}</script>
</head>
<body>

<div class="top-bar">
    <div class="top-bar-content flex-col md:flex-row md:justify-between text-center md:text-left gap-1 md:gap-0">
        <div class="top-bar-links">
            <a href="index.php">Портал</a>
            <a href="download.php">Скачать</a>
        </div>
        <div class="top-bar-auth">
            <?php if (isset($_SESSION['user_id'])): ?>
                <span>Здравствуйте, <a href="profile.php"><?php echo htmlspecialchars($_SESSION['username']); ?></a></span>
                <?php if (isset($_SESSION['is_admin']) && $_SESSION['is_admin']): ?>
                    | <a href="admin.php">Админ-панель</a>
                <?php endif; ?>
                | <a href="logout.php" class="logout">Выйти</a>
            <?php else: ?>
                <a href="login.php">Войти</a> или <a href="register.php">Создать аккаунт</a>
            <?php endif; ?>
        </div>
    </div>
</div>

<div class="header-banner h-[100px] md:h-[180px]">
    <div class="logo-container gap-2 md:gap-[18px]">
        <img src="images/logo.png" alt="Logo" class="logo-icon w-10 h-10 md:w-[72px] md:h-[72px]">
        <div class="logo-text-wrapper">
            <div class="logo-text text-xl md:text-4xl">World of Tanks</div>
            <div class="logo-subtext text-[9px] md:text-sm">Project Orion 0.6.5</div>
        </div>
    </div>
</div>

<div class="nav-container">
    <button class="nav-hamburger" onclick="document.getElementById('navMenu').classList.toggle('open')" aria-label="Меню">&#9776;</button>
    <ul class="nav-menu" id="navMenu">
        <li class="nav-item"><a href="index.php" class="nav-link">Главная</a></li>
        <li class="nav-item"><a href="download.php" class="nav-link active">Играть</a></li>
        <li class="nav-item"><a href="register.php" class="nav-link">Регистрация</a></li>
        <li class="nav-item"><a href="profile.php" class="nav-link">Личный кабинет</a></li>
        <li class="nav-item"><a href="bugs.php" class="nav-link">Баг-репорты</a></li>
        <?php if (isset($_SESSION['is_admin']) && $_SESSION['is_admin']): ?>
            <li class="nav-item"><a href="admin.php" class="nav-link">Управление танками</a></li>
        <?php endif; ?>
    </ul>
</div>

<div class="main-layout flex-col items-center" style="justify-content: center;">
    <div class="content-area content-area--wide w-full">
        <div class="card">
            <div class="card-header">
                <div class="card-title text-sm md:text-lg">Скачать клиент игры</div>
            </div>
            <div class="card-body">
                <div class="dl-box">
                    <div class="dl-title text-base md:text-xl">Полный клиент World of Tanks v.0.6.5</div>
                    <div class="dl-meta">Размер: ~2.4 ГБ | Формат: ZIP-архив | Версия: 0.6.5 (2011 год)</div>
                    <a href="https://mega.nz/file/XqI2AI5S#4rjABHtHgNhcwbjMhAunvHQXaiCgSSKzLpybNMDGfrU" target="_blank" class="btn btn-primary" style="font-size: 16px; padding: 12px 30px;">Скачать клиент (Mega.nz)</a>
                    <a href="https://fex.net/ru/s/p0lvm0f" target="_blank" class="btn btn-primary" style="font-size: 16px; padding: 12px 30px; margin-left: 10px;">Скачать клиент (Fex.net)</a>
                    <a href="https://limewire.com/d/Sfct4#4jZNLmzBhV" target="_blank" class="btn btn-primary" style="font-size: 16px; padding: 12px 30px; margin-left: 10px;">Скачать клиент (LimeWire)</a>
                    <a href="https://disk.yandex.ru/d/9USaIQXNZB-pAQ" target="_blank" class="btn btn-primary" style="font-size: 16px; padding: 12px 30px; margin-left: 10px;">Скачать клиент (Яндекс Диск)</a>
                </div>

                <div style="font-size: 18px; font-weight: 600; color: #e5a93b; text-transform: uppercase; margin-top: 30px; margin-bottom: 15px; border-bottom: 1px solid #28282a; padding-bottom: 8px;">Инструкция по установке</div>

                <p style="color: #bbbbbb; font-size: 14px; margin-bottom: 18px;">Подробная инструкция по установке показана в видео ниже:</p>

                <div style="position: relative; width: 100%; border-radius: 6px; overflow: hidden; border: 1px solid #28282a; box-shadow: 0 4px 16px rgba(0,0,0,0.5);">
                    <video controls preload="metadata" style="width: 100%; display: block; background: #000;">
                        <source src="video/установка_проект_орион_марсианин.mp4" type="video/mp4">
                        Ваш браузер не поддерживает воспроизведение видео.
                    </video>
                </div>
            </div>
        </div>
    </div>
</div>

<div class="footer text-[11px] md:text-xs px-3 md:px-0">
    <p>&copy; 2026 World of Tanks Project Orion 0.6.5. Все права защищены.</p>
    <p>Project Orion является некоммерческим фанатским проектом и не претендует на права Wargaming.</p>
</div>

<script src="sparks.js"></script>
</body>
</html>

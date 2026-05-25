<?php
require_once 'db.php';

$total_accounts = 0;
$total_battles = 0;
$total_wins = 0;

try {
    $stmt = $pdo->query("SELECT COUNT(*) FROM accounts");
    $total_accounts = $stmt->fetchColumn();

    $stmt = $pdo->query("SELECT COUNT(*) FROM battles");
    $total_battles = $stmt->fetchColumn();

    $stmt = $pdo->query("SELECT SUM(wins) FROM dossier");
    $total_wins = intval($stmt->fetchColumn());
} catch (Exception $e) {
}

$active_page = 'index';
?>
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>World of Tanks Project Orion 0.6.5 - Главная</title>
    <link rel="stylesheet" href="style.css">
    <link rel="icon" type="image/png" href="favicon.png">
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
        tailwind.config = {
            theme: {
                extend: {
                    colors: {
                        wot: { gold: '#e5a93b', dark: '#1a1a1c', panel: '#101011' }
                    }
                }
            }
        }
    </script>
    <style>
        .modal-overlay { transition: opacity 0.3s ease; }
        .modal-content { transition: transform 0.3s ease, opacity 0.3s ease; }
        .modal-overlay.hidden { opacity: 0; pointer-events: none; }
        .modal-overlay.hidden .modal-content { transform: scale(0.9) translateY(20px); opacity: 0; }
    </style>
</head>
<body>

<div class="top-bar">
    <div class="top-bar-content">
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

<div class="header-banner">
    <div class="logo-container">
        <img src="images/logo.png" alt="Logo" class="logo-icon">
        <div class="logo-text-wrapper">
            <div class="logo-text">World of Tanks</div>
            <div class="logo-subtext">Project Orion 0.6.5</div>
        </div>
    </div>
</div>

<div class="nav-container">
    <ul class="nav-menu">
        <li class="nav-item"><a href="index.php" class="nav-link active">Главная</a></li>
        <li class="nav-item"><a href="download.php" class="nav-link">Играть</a></li>
        <li class="nav-item"><a href="register.php" class="nav-link">Регистрация</a></li>
        <li class="nav-item"><a href="profile.php" class="nav-link">Личный кабинет</a></li>
        <?php if (isset($_SESSION['is_admin']) && $_SESSION['is_admin']): ?>
            <li class="nav-item"><a href="admin.php" class="nav-link">Управление танками</a></li>
        <?php endif; ?>
    </ul>
</div>

<div class="main-layout">
    <div class="content-area">
        <div class="hero-slider">
            <img src="images/wot_banner.png" alt="Мир Танков 0.6.5">
            <div class="hero-overlay">
                <div class="hero-title">Масштабные танковые бои 2011 года!</div>
                <div class="hero-desc">Соберите команду, выберите легендарный Lowe, Т-34 или Maus и окунитесь в атмосферу классической физики и геймплея 0.6.5!</div>
            </div>
        </div>

        <div class="card">
            <div class="card-header">
                <div class="card-title">Новости сервера</div>
            </div>
            <div class="card-body">
                <div class="news-list">
                    <div class="news-item">
                        <img src="images/wot_banner.png" class="news-img" alt="Новость 1">
                        <div class="news-info">
                            <a href="#" class="news-title">Запуск классического сервера 0.6.5!</a>
                            <div class="news-meta">
                                <span>Сегодня, 15:30</span>
                                <span>Администрация</span>
                            </div>
                            <div class="news-text">
                                Рады объявить об успешном открытии сервера классической версии игры! Вас ждут старые карты, знакомый баланс и ламповая атмосфера начала эпохи танковых баталий. Скачивайте клиент в разделе "Играть"!
                            </div>
                        </div>
                    </div>
                    <div class="news-item">
                        <img src="images/wot_banner.png" class="news-img" alt="Новость 2">
                        <div class="news-info">
                            <a href="#" class="news-title">Обновленная система управления танками в админке</a>
                            <div class="news-meta">
                                <span>Вчера, 12:45</span>
                                <span>Технический отдел</span>
                            </div>
                            <div class="news-text">
                                Мы добавили удобную панель для администраторов, которая позволяет включать и отключать отдельные танки в игре в реальном времени. Это поможет точнее балансировать игровые сессии во время событий.
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <div class="sidebar-area">
        <a href="download.php" class="play-btn">Играть бесплатно</a>

        <div class="card">
            <div class="card-header">
                <div class="card-title">Статистика сервера</div>
            </div>
            <div class="card-body">
                <div class="stat-row">
                    <span class="stat-label">Всего танкистов:</span>
                    <span class="stat-value"><?php echo $total_accounts; ?></span>
                </div>
                <div class="stat-row">
                    <span class="stat-label">Сыграно боев:</span>
                    <span class="stat-value"><?php echo $total_battles; ?></span>
                </div>
                <div class="stat-row">
                    <span class="stat-label">Всего побед:</span>
                    <span class="stat-value"><?php echo $total_wins; ?></span>
                </div>
                <div class="stat-row">
                    <span class="stat-label">Статус сервера:</span>
                    <span class="stat-value status-active">Онлайн</span>
                </div>
            </div>
        </div>

        <div class="card">
            <div class="card-header">
                <div class="card-title">Опрос</div>
            </div>
            <div class="card-body">
                <div style="font-weight: 600; margin-bottom: 15px; color: #ffffff;">Вам нравится версия 0.6.5?</div>
                
                <div class="poll-option">
                    <div class="poll-option-label">
                        <span>Да, это лучшие времена!</span>
                        <span>82.4%</span>
                    </div>
                    <div class="poll-bar-container">
                        <div class="poll-bar" style="width: 82.4%;"></div>
                    </div>
                </div>

                <div class="poll-option">
                    <div class="poll-option-label">
                        <span>Нет, современная игра лучше.</span>
                        <span>17.6%</span>
                    </div>
                    <div class="poll-bar-container">
                        <div class="poll-bar" style="width: 17.6%;"></div>
                    </div>
                </div>
            </div>
        </div>

        <div class="card">
            <div class="card-header">
                <div class="card-title">Мы в соцсетях</div>
            </div>
            <div class="card-body">
                <div class="social-links">
                    <a href="#" class="social-btn social-vk">ВКонтакте</a>
                    <a href="#" class="social-btn social-twitter">Twitter</a>
                    <a href="#" class="social-btn social-youtube">YouTube</a>
                </div>
            </div>
        </div>
    </div>
</div>

<div class="footer">
    <p>&copy; 2026 World of Tanks Project Orion 0.6.5. Все права защищены.</p>
    <p>Сайт создан для демонстрации и тестирования Project Orion 0.6.5.</p>
    <p>Project Orion является некоммерческим фанатским проектом и не претендует на права Wargaming.</p>
</div>

<!-- Modal -->
<div id="donateModal" class="modal-overlay fixed inset-0 z-50 flex items-center justify-center bg-black/70">
    <div class="modal-content relative bg-gradient-to-b from-[#1e1e20] to-[#101011] border border-[#e5a93b]/40 rounded-2xl shadow-2xl shadow-[#e5a93b]/10 max-w-md w-full mx-4 p-0 overflow-hidden">
        <button onclick="closeModal()" class="absolute top-3 right-3 text-gray-500 hover:text-white transition-colors text-2xl leading-none z-10">&times;</button>
        <div class="bg-gradient-to-r from-[#e5a93b]/20 to-transparent px-6 pt-6 pb-4 border-b border-[#e5a93b]/20">
            <div class="flex items-center gap-3">
                <div class="w-10 h-10 rounded-full bg-[#e5a93b]/20 flex items-center justify-center text-[#e5a93b] text-xl font-bold">&#9733;</div>
                <div>
                    <h2 class="text-white text-xl font-bold tracking-tight">Поддержи проект</h2>
                    <p class="text-gray-400 text-sm">Project Orion 0.6.5</p>
                </div>
            </div>
        </div>
        <div class="px-6 py-5 space-y-5">
            <p class="text-gray-300 text-sm leading-relaxed">
                Сервер живёт и развивается благодаря вашей поддержке. Если тебе нравится проект, ты можешь помочь с оплатой хостинга и разработкой новых возможностей.
            </p>
            <div class="bg-black/30 rounded-xl p-4 border border-[#e5a93b]/10">
                <div class="flex items-center gap-2 text-[#e5a93b] text-sm font-semibold mb-3">
                    <span class="text-lg">&#9829;</span>
                    <span>Варианты поддержки</span>
                </div>
                <ul class="space-y-2 text-gray-400 text-sm">
                    <li class="flex items-start gap-2">
                        <span class="text-[#e5a93b] mt-0.5">&#9672;</span>
                        <span>Разовое пожертвование через DonationAlerts</span>
                    </li>
                    <li class="flex items-start gap-2">
                        <span class="text-[#e5a93b] mt-0.5">&#9672;</span>
                        <span>Регулярная поддержка развития сервера</span>
                    </li>
                    <li class="flex items-start gap-2">
                        <span class="text-[#e5a93b] mt-0.5">&#9672;</span>
                        <span>Твой донат помогает с хостингом и нововведениями</span>
                    </li>
                </ul>
            </div>
            <a href="https://www.donationalerts.com/r/verffexcrf" target="_blank" class="block w-full py-3 bg-gradient-to-r from-[#e5a93b] to-[#d4941f] hover:from-[#f0b84d] hover:to-[#daa12a] text-black font-bold text-center rounded-xl transition-all duration-200 shadow-lg shadow-[#e5a93b]/20 hover:shadow-[#e5a93b]/40">
                &#9829;&nbsp;&nbsp;Задонатить на проект
            </a>
            <p class="text-gray-500 text-xs text-center">
                Спасибо, что ты с нами! Каждая подписка и донат помогают серверу расти.
            </p>
        </div>
        <div class="px-6 pb-4 flex justify-center">
            <button onclick="closeModal()" class="text-gray-500 hover:text-white text-sm transition-colors">Закрыть</button>
        </div>
    </div>
</div>

<script>
function closeModal() {
    document.getElementById('donateModal').classList.add('hidden');
}
</script>

</body>
</html>

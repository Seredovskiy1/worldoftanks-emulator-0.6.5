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
    error_log("Index stats query: " . $e->getMessage());
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
            important: true,
            theme: {
                extend: {
                    colors: {
                        wot: { gold: '#e5a93b', dark: '#1a1a1c', panel: '#101011' }
                    }
                }
            }
        }
    </script>
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
        <li class="nav-item"><a href="index.php" class="nav-link active">Главная</a></li>
        <li class="nav-item"><a href="download.php" class="nav-link">Играть</a></li>
        <li class="nav-item"><a href="register.php" class="nav-link">Регистрация</a></li>
        <li class="nav-item"><a href="profile.php" class="nav-link">Личный кабинет</a></li>
        <li class="nav-item"><a href="bugs.php" class="nav-link">Баг-репорты</a></li>
        <?php if (isset($_SESSION['is_admin']) && $_SESSION['is_admin']): ?>
            <li class="nav-item"><a href="admin.php" class="nav-link">Управление танками</a></li>
        <?php endif; ?>
    </ul>
</div>

<div class="main-layout flex-col md:flex-row">
    <div class="content-area w-full md:w-[70%]">
        <div class="hero-slider h-[180px] md:h-[300px]">
            <img src="images/wot_banner.png" alt="Мир Танков 0.6.5">
            <div class="hero-overlay">
                <div class="hero-title text-base md:text-2xl">Масштабные танковые бои 2011 года!</div>
                <div class="hero-desc text-xs md:text-sm">Соберите команду, выберите легендарный Lowe, Т-34 или Maus и окунитесь в атмосферу классической физики и геймплея 0.6.5!</div>
            </div>
        </div>

        <div class="card">
            <div class="card-header">
                <div class="card-title text-sm md:text-lg">Новости сервера</div>
            </div>
            <div class="card-body">
                <div class="news-list">
                    <div class="news-item flex-col md:flex-row">
                        <img src="images/wot_banner.png" class="news-img w-full md:w-[120px] h-[100px] md:h-[90px]" alt="Новость 1">
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
                    <div class="news-item flex-col md:flex-row">
                        <img src="images/wot_banner.png" class="news-img w-full md:w-[120px] h-[100px] md:h-[90px]" alt="Новость 2">
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

        <!-- Donate banner -->
        <button
            onclick="openModal()"
            class="w-full flex flex-col sm:flex-row items-center gap-4 text-left relative overflow-hidden rounded-lg p-5 border border-yellow-700/30 bg-gradient-to-r from-yellow-950/70 via-yellow-900/30 to-transparent cursor-pointer transition-all duration-200 hover:border-yellow-600/50 hover:shadow-lg hover:shadow-yellow-900/30 group"
        >
            <span class="absolute inset-0 pointer-events-none bg-[radial-gradient(ellipse_at_left,rgba(229,169,59,0.12),transparent_65%)]" aria-hidden="true"></span>
            <span class="text-yellow-400 text-5xl leading-none select-none flex-shrink-0 drop-shadow-[0_0_12px_rgba(229,169,59,0.5)] group-hover:scale-110 transition-transform duration-200">❤</span>
            <span class="flex-1 min-w-0">
                <span class="block text-white font-black text-sm uppercase tracking-widest mb-1">Поддержи Project Orion 0.6.5</span>
                <span class="block text-gray-400 text-[13px] leading-snug">Сервер живёт и развивается благодаря вам. Каждый донат помогает с хостингом и новыми возможностями!</span>
            </span>
            <span class="flex-shrink-0 relative overflow-hidden bg-gradient-to-b from-yellow-400 to-yellow-600 group-hover:from-yellow-300 group-hover:to-yellow-500 active:scale-95 text-black font-black text-[13px] uppercase tracking-widest px-6 py-3 rounded-lg shadow-lg shadow-yellow-900/40 group-hover:shadow-yellow-800/60 transition-all duration-200 group-hover:-translate-y-0.5 whitespace-nowrap">
                <span class="relative z-10">❤  Задонатить</span>
                <span class="absolute inset-0 bg-white/25 translate-x-[-110%] skew-x-[-20deg] group-hover:translate-x-[130%] transition-transform duration-500 pointer-events-none" aria-hidden="true"></span>
            </span>
        </button>

    </div>

    <div class="sidebar-area w-full md:w-[30%]">
        <a href="download.php" class="play-btn text-sm md:text-lg text-center">Играть бесплатно</a>

        <div class="card">
            <div class="card-header">
                <div class="card-title text-sm md:text-lg">Статистика сервера</div>
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
                <div class="card-title text-sm md:text-lg">Опрос</div>
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
                <div class="card-title text-sm md:text-lg">Мы в соцсетях</div>
            </div>
            <div class="card-body">
                <div class="social-links">
                    <a href="#" class="social-btn social-vk">ВКонтакте</a>
                    <a href="#" class="social-btn social-twitter">Twitter</a>
                    <a href="#" class="social-btn social-youtube">YouTube</a>
                </div>
            </div>
        </div>

        <div class="card">
            <div class="card-header">
                <div class="card-title text-sm md:text-lg">Поддержка</div>
            </div>
            <div class="card-body">
                <p style="font-size: 13px; color: #aaaaaa; margin-bottom: 12px;">
                    Сервер живёт и развивается благодаря вашей поддержке. Если тебе нравится проект, ты можешь помочь с оплатой хостинга.
                </p>
                <button onclick="openModal()" class="btn btn-primary" style="width: 100%;">
                    &#9829;&nbsp;&nbsp;Поддержать проект
                </button>
            </div>
        </div>
    </div>
</div>

<div class="footer text-[11px] md:text-xs px-3 md:px-0">
    <p>&copy; 2026 World of Tanks Project Orion 0.6.5. Все права защищены.</p>
    <p>Сайт создан для демонстрации и тестирования Project Orion 0.6.5.</p>
    <p>Project Orion является некоммерческим фанатским проектом и не претендует на права Wargaming.</p>
</div>

<!-- Donate Modal -->
<div id="donateModal" class="donate-modal">
    <div class="donate-modal-bg" onclick="closeModal()"></div>
    <div class="donate-modal-content">
        <button onclick="closeModal()" class="donate-modal-close">&times;</button>
        <div class="donate-modal-body">
            <div class="donate-modal-header">
                <div class="donate-modal-icon">&#9733;</div>
                <div>
                    <h2 class="donate-modal-title">Поддержи проект</h2>
                    <p class="donate-modal-subtitle">Project Orion 0.6.5</p>
                </div>
            </div>
            <p class="donate-modal-text">
                Сервер живёт и развивается благодаря вашей поддержке. Если тебе нравится проект, ты можешь помочь с оплатой хостинга и разработкой новых возможностей.
            </p>
            <div class="donate-modal-features">
                <div class="donate-modal-feature">
                    <span class="donate-modal-dot">&#9672;</span>
                    <span>Разовое пожертвование через DonationAlerts</span>
                </div>
                <div class="donate-modal-feature">
                    <span class="donate-modal-dot">&#9672;</span>
                    <span>Регулярная поддержка развития сервера</span>
                </div>
                <div class="donate-modal-feature">
                    <span class="donate-modal-dot">&#9672;</span>
                    <span>Твой донат помогает с хостингом и нововведениями</span>
                </div>
            </div>
            <a href="https://www.donationalerts.com/r/verffexcrf" target="_blank" class="donate-modal-btn">
                &#9829;&nbsp;&nbsp;Задонатить на проект
            </a>
            <p class="donate-modal-footer-text">
                Спасибо, что ты с нами! Каждая подписка и донат помогают серверу расти.
            </p>
        </div>
        <div class="donate-modal-actions">
            <button onclick="closeModal()" class="donate-modal-cancel">Закрыть</button>
        </div>
    </div>
</div>

<script>
function openModal() {
    document.getElementById('donateModal').classList.add('show');
    document.body.style.overflow = 'hidden';
}

function closeModal() {
    document.getElementById('donateModal').classList.remove('show');
    document.body.style.overflow = '';
}

// Auto-show modal after 1 second
document.addEventListener('DOMContentLoaded', function() {
    openModal();
});
</script>

</body>
</html>
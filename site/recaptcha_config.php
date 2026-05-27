<?php
/**
 * Google reCAPTCHA v2 Configuration
 * 
 * Отримайте ключі тут: https://www.google.com/recaptcha/admin
 * (адмінка Google, увійдіть під своїм Google-акаунтом)
 * 
 * Інструкція:
 * 1. Перейдіть за посиланням: https://www.google.com/recaptcha/admin
 * 2. Увійдіть у свій Google-акаунт
 * 3. Натисніть "Create" / "Создать" / "Створити"
 * 4. Виберіть reCAPTCHA v2 ("I'm not a robot")
 * 5. Додайте свій домен (для локального тестування: localhost)
 * 6. Скопіюйте отримані Site key та Secret key сюди
 * 7. Готово!
 */

define('RECAPTCHA_SITE_KEY', getenv('RECAPTCHA_SITE_KEY') ?: '6LdrRPwsAAAAAForngYBVG6xOzb1sOpHKyewkG5i');
define('RECAPTCHA_SECRET_KEY', getenv('RECAPTCHA_SECRET_KEY') ?: '6LdrRPwsAAAAAPxHSRlsVxvagmTr3JNSEtFwfE2U');

// reCAPTCHA v2 не використовує scoring — просто success/fail
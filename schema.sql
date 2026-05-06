-- ============================================================================
--  World of Tanks 0.6.5 emulator -- MySQL / MariaDB schema
--  Import via phpMyAdmin or:
--      mysql -u root -p < schema.sql
--  Default DB name: wot_emulator (override with MYSQL_DB env var on the server)
-- ============================================================================

CREATE DATABASE IF NOT EXISTS `wot_emulator`
    DEFAULT CHARACTER SET utf8mb4
    DEFAULT COLLATE utf8mb4_unicode_ci;

USE `wot_emulator`;

-- ----------------------------------------------------------------------------
--  accounts -- player profile + persistent currency / progression
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `accounts` (
    `id`                 BIGINT UNSIGNED  NOT NULL AUTO_INCREMENT,
    `username`           VARCHAR(32)      NOT NULL,
    `normalized_name`    VARCHAR(32)      NOT NULL,
    `password_hash`      CHAR(64)         NOT NULL,
    `created_at`         DATETIME         NOT NULL,
    `last_login`         DATETIME         NOT NULL,
    `credits`            BIGINT           NOT NULL DEFAULT 1000000000,
    `gold`               BIGINT           NOT NULL DEFAULT 1000000,
    `free_xp`            BIGINT           NOT NULL DEFAULT 1000000,
    `slots`              INT              NOT NULL DEFAULT 200,
    `berths`             INT              NOT NULL DEFAULT 50,
    `premium_expire_at`  BIGINT           NOT NULL DEFAULT 0,
    `attrs`              BIGINT           NOT NULL DEFAULT 0,
    `clan_db_id`         BIGINT           NOT NULL DEFAULT 0,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uniq_username`        (`username`),
    UNIQUE KEY `uniq_normalized_name` (`normalized_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ----------------------------------------------------------------------------
--  account_unlocks -- compact descriptors of items the player has unlocked
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `account_unlocks` (
    `account_id`          BIGINT UNSIGNED NOT NULL,
    `item_compact_descr`  BIGINT          NOT NULL,
    PRIMARY KEY (`account_id`, `item_compact_descr`),
    CONSTRAINT `fk_unlocks_account`
        FOREIGN KEY (`account_id`) REFERENCES `accounts`(`id`)
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ----------------------------------------------------------------------------
--  account_elite_vehicles -- vehicles that have reached elite status
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `account_elite_vehicles` (
    `account_id`             BIGINT UNSIGNED NOT NULL,
    `vehicle_compact_descr`  BIGINT          NOT NULL,
    PRIMARY KEY (`account_id`, `vehicle_compact_descr`),
    CONSTRAINT `fk_elite_account`
        FOREIGN KEY (`account_id`) REFERENCES `accounts`(`id`)
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ----------------------------------------------------------------------------
--  account_double_xp_vehicles -- vehicles eligible for x2 XP next battle
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `account_double_xp_vehicles` (
    `account_id`             BIGINT UNSIGNED NOT NULL,
    `vehicle_compact_descr`  BIGINT          NOT NULL,
    PRIMARY KEY (`account_id`, `vehicle_compact_descr`),
    CONSTRAINT `fk_doublexp_account`
        FOREIGN KEY (`account_id`) REFERENCES `accounts`(`id`)
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ----------------------------------------------------------------------------
--  battles -- one row per started match
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `battles` (
    `id`             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `arena_type_id`  INT             NOT NULL,
    `queue_type`     INT             NOT NULL DEFAULT 0,
    `created_at`     DATETIME        NOT NULL,
    `finished_at`    DATETIME            NULL,
    `winner_team`    TINYINT             NULL,
    `finish_reason`  TINYINT             NULL,
    PRIMARY KEY (`id`),
    KEY `idx_battles_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ----------------------------------------------------------------------------
--  battle_entries -- which account joined which battle, with which tank
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `battle_entries` (
    `battle_id`       BIGINT UNSIGNED NOT NULL,
    `account_id`      BIGINT UNSIGNED NOT NULL,
    `vehicle_inv_id`  INT             NOT NULL,
    `team`            TINYINT         NOT NULL DEFAULT 0,
    `joined_at`       DATETIME        NOT NULL,
    PRIMARY KEY (`battle_id`, `account_id`),
    KEY `idx_entries_account` (`account_id`),
    CONSTRAINT `fk_entries_battle`
        FOREIGN KEY (`battle_id`)  REFERENCES `battles`(`id`)
        ON DELETE CASCADE,
    CONSTRAINT `fk_entries_account`
        FOREIGN KEY (`account_id`) REFERENCES `accounts`(`id`)
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ----------------------------------------------------------------------------
--  battle_results -- one row per (battle, account) when match ends
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `battle_results` (
    `id`               BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `battle_id`        BIGINT UNSIGNED NOT NULL,
    `account_id`       BIGINT UNSIGNED NOT NULL,
    `vehicle_inv_id`   INT             NOT NULL,
    `is_winner`        TINYINT         NOT NULL DEFAULT 0,
    `frags`            INT             NOT NULL DEFAULT 0,
    `damage_dealt`     INT             NOT NULL DEFAULT 0,
    `damage_received`  INT             NOT NULL DEFAULT 0,
    `shots`            INT             NOT NULL DEFAULT 0,
    `hits`             INT             NOT NULL DEFAULT 0,
    `life_time_sec`    INT             NOT NULL DEFAULT 0,
    `credits_earned`   INT             NOT NULL DEFAULT 0,
    `xp_earned`        INT             NOT NULL DEFAULT 0,
    `free_xp_earned`   INT             NOT NULL DEFAULT 0,
    `finished_at`      DATETIME        NOT NULL,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uniq_battle_account` (`battle_id`, `account_id`),
    KEY `idx_results_account` (`account_id`),
    CONSTRAINT `fk_results_battle`
        FOREIGN KEY (`battle_id`)  REFERENCES `battles`(`id`)
        ON DELETE CASCADE,
    CONSTRAINT `fk_results_account`
        FOREIGN KEY (`account_id`) REFERENCES `accounts`(`id`)
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ----------------------------------------------------------------------------
--  dossier -- aggregated per-account career statistics
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `dossier` (
    `account_id`       BIGINT UNSIGNED NOT NULL,
    `total_battles`    INT             NOT NULL DEFAULT 0,
    `wins`             INT             NOT NULL DEFAULT 0,
    `losses`           INT             NOT NULL DEFAULT 0,
    `draws`            INT             NOT NULL DEFAULT 0,
    `frags`            INT             NOT NULL DEFAULT 0,
    `damage_dealt`     BIGINT          NOT NULL DEFAULT 0,
    `damage_received`  BIGINT          NOT NULL DEFAULT 0,
    `shots`            INT             NOT NULL DEFAULT 0,
    `hits`             INT             NOT NULL DEFAULT 0,
    `max_xp`           INT             NOT NULL DEFAULT 0,
    `max_damage`       INT             NOT NULL DEFAULT 0,
    `max_frags`        INT             NOT NULL DEFAULT 0,
    `total_xp`         BIGINT          NOT NULL DEFAULT 0,
    `total_credits`    BIGINT          NOT NULL DEFAULT 0,
    `last_battle_at`   DATETIME            NULL,
    PRIMARY KEY (`account_id`),
    CONSTRAINT `fk_dossier_account`
        FOREIGN KEY (`account_id`) REFERENCES `accounts`(`id`)
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Создание таблицы ролей и добавление базовых ролей
CREATE TABLE IF NOT EXISTS role (
    id SERIAL PRIMARY KEY,
    name VARCHAR NOT NULL,
    permissions JSONB
);

-- Вставляем базовые роли
INSERT INTO role (id, name, permissions) VALUES
(1, 'user', '["read", "write"]'),
(2, 'admin', '["read", "write", "delete"]')
ON CONFLICT (id) DO NOTHING;
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS customers (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    phone TEXT,
    email TEXT NOT NULL UNIQUE,
    amount_paid REAL NOT NULL DEFAULT 0,
    payment_date TEXT,
    subscription_start TEXT,
    subscription_end TEXT,
    plan_duration TEXT,
    status TEXT NOT NULL DEFAULT 'Active',
    notes TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS subscriptions (
    id SERIAL PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE RESTRICT,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    plan_duration TEXT,
    amount_paid REAL DEFAULT 0,
    payment_date TEXT,
    status TEXT NOT NULL DEFAULT 'Active',
    notes TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS parent_accounts (
    id SERIAL PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    capacity INTEGER NOT NULL DEFAULT 5 CHECK(capacity >= 1),
    status TEXT NOT NULL DEFAULT 'Active',
    notes TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS parent_subscriptions (
    id SERIAL PRIMARY KEY,
    parent_id INTEGER NOT NULL REFERENCES parent_accounts(id) ON DELETE RESTRICT,
    start_date TEXT NOT NULL,
    expiry_date TEXT NOT NULL,
    capacity INTEGER,
    status TEXT NOT NULL DEFAULT 'Active',
    notes TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS monthly_assignments (
    id SERIAL PRIMARY KEY,
    month TEXT NOT NULL,
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE RESTRICT,
    parent_subscription_id INTEGER NOT NULL REFERENCES parent_subscriptions(id) ON DELETE RESTRICT,
    customer_added_date TEXT NOT NULL,
    customer_removed_date TEXT,
    family_subscription_start_date TEXT NOT NULL,
    family_subscription_expiry_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Active',
    notes TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_customers_name ON customers(name);
CREATE INDEX IF NOT EXISTS idx_customers_phone ON customers(phone);
CREATE INDEX IF NOT EXISTS idx_customers_email ON customers(email);
CREATE INDEX IF NOT EXISTS idx_assignments_customer ON monthly_assignments(customer_id);
CREATE INDEX IF NOT EXISTS idx_assignments_month ON monthly_assignments(month);
CREATE INDEX IF NOT EXISTS idx_parent_subscriptions_parent ON parent_subscriptions(parent_id);

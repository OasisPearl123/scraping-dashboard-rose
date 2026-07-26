-- Create system_config table to store sensitive configuration
CREATE TABLE IF NOT EXISTS system_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Insert default admin PIN
INSERT INTO system_config (key, value)
VALUES ('admin_pin', '2323')
ON CONFLICT (key) DO NOTHING;

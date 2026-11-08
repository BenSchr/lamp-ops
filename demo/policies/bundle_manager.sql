SELECT 'bundle' AS resource_key, 'A CAN_MANAGE bundle permission is required' AS message
FROM summary
WHERE NOT EXISTS (
    SELECT 1 FROM permissions WHERE level = 'CAN_MANAGE'
);

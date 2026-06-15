-- 0017: peripheral prerequisites for semantic intents (v3.1 Gap 4).
-- A network protocol needs a physical NIC, not just free flash. prerequisites_json is a JSON list
-- of board capabilities, ANY ONE of which satisfies the intent (e.g. ["wifi","ethernet"]). An empty
-- list means no peripheral is required (compute/crypto/kernel stacks).
ALTER TABLE semantic_cost_estimates ADD COLUMN prerequisites_json TEXT NOT NULL DEFAULT '[]';

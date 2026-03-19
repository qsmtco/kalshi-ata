import sys, os
sys.path.insert(0, 'src')
from settings_manager import SettingsManager

db_path = 'data/test_settings.db'
if os.path.exists(db_path):
    os.remove(db_path)

sm = SettingsManager(db_path=db_path)

# Test defaults
assert sm.get('kellyFraction') == 0.5
assert sm.get('maxPositionSizePct') == 0.1

# Test valid update
old = sm.get('kellyFraction')
sm.update('kellyFraction', 0.6, 'test', 'test')
assert sm.get('kellyFraction') == 0.6

# Verify history
hist = sm.get_history('kellyFraction', limit=1)
assert len(hist) == 1
assert hist[0]['old_value'] == str(old)
assert hist[0]['new_value'] == '0.6'
assert hist[0]['source'] == 'test'

# Test guardrail violation
try:
    sm.update('kellyFraction', 0.95, 'test', 'should fail')
    assert False, "Should have raised"
except ValueError as e:
    assert 'outside' in str(e).lower() or 'guardrail' in str(e).lower()

print("✓ SettingsManager basic test passed")
os.remove(db_path)

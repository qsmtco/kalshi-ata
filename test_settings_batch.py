import sys, os
sys.path.insert(0, 'src')
from settings_manager import SettingsManager

db_path = 'data/test_batch.db'
if os.path.exists(db_path): os.remove(db_path)

sm = SettingsManager(db_path)

# get_settings returns dict
all = sm.get_settings()
assert 'kellyFraction' in all
assert all['kellyFraction'] == 0.5

# batch update
result = sm.update_settings({'kellyFraction': 0.6, 'maxPositionSizePct': 0.15})
assert result['success'] == True
assert set(result['updated']) == {'kellyFraction', 'maxPositionSizePct'}

# verify
new = sm.get_settings()
assert new['kellyFraction'] == 0.6
assert new['maxPositionSizePct'] == 0.15

print("✓ SettingsManager batch test passed")
os.remove(db_path)

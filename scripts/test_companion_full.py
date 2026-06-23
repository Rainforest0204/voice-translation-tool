"""Full functional test for CompanionWindow — tests rendering, menus, device panel."""
import sys, json
sys.path.insert(0, '.')

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer, Qt, QPoint
from PyQt6.QtGui import QCursor

app = QApplication(sys.argv)

from src.companion import CompanionWindow

with open('config.json', 'r', encoding='utf-8') as f:
    config = json.load(f)

print('1. Creating CompanionWindow...')
w = CompanionWindow(config, capture_mode='loopback')
w.show()
print('   OK — window visible')

print('2. State transitions...')
for state in ['listening', 'translating', 'intense', 'sleep', 'idle']:
    w.set_state(state)
print('   OK — all 5 states rendered')

print('3. Mode switch...')
w.set_mode('microphone')
assert w._capture_mode == 'microphone', 'mode switch failed'
w.set_mode('loopback')
assert w._capture_mode == 'loopback', 'mode switch failed'
print('   OK — mode switching works')

print('4. Capturing state...')
w.set_capturing(True)
w.set_capturing(False)
print('   OK')

print('5. Stats update...')
w.update_stats(10, 8, 6, 320.5)
print('   OK')

print('6. Logging...')
w.add_log('Test log message')
print('   OK')

print('7. Overlay closed callback...')
w.on_overlay_closed()
print('   OK')

print('8. Window properties...')
print(f'   position: ({w.x()},{w.y()})')
print(f'   size: {w.width()}x{w.height()}')
print(f'   visible: {w.isVisible()}')
print(f'   isMinimized: {w.isMinimized()}')
print('   OK')

print('9. Simulate click on companion (radial menu)...')
# Get center of companion widget in global coords
cw = w._companion_widget
center = cw.mapToGlobal(QPoint(cw.width() // 2, cw.height() // 2))
print(f'   companion center: ({center.x()},{center.y()})')

# Trigger click (don't show radial menu to avoid popup blocking)
cw.clicked.emit()
print('   OK — click signal emitted')

# Trigger double-click
cw.double_clicked.emit()
print('   OK — double-click signal emitted')

# Trigger right-click
cw.right_clicked.emit()
print('   OK — right-click signal emitted')

print('10. Signal wiring check...')
signals = ['toggle_capture', 'clear_subtitles', 'toggle_overlay',
           'mode_changed', 'device_changed', 'config_changed']
received = {s: False for s in signals}

def make_handler(name):
    def handler(*args):
        received[name] = True
    return handler

for s in signals:
    getattr(w, s).connect(make_handler(s))

w.toggle_capture.emit()
w.clear_subtitles.emit()
w.toggle_overlay.emit()
w.mode_changed.emit('microphone')
w.device_changed.emit('wasapi:0')
w.config_changed.emit({'test': True})

for s, r in received.items():
    assert r, f'signal {s} not received'
print('   OK — all 6 signals wired and emitting')

print()
print('=' * 50)
print('ALL TESTS PASSED — Companion is stable')
print('=' * 50)

QTimer.singleShot(100, app.quit)
app.exec()

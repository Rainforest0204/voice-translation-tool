"""Quick smoke test for CompanionWindow."""
import sys
import json

sys.path.insert(0, '.')

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer

app = QApplication(sys.argv)

from src.companion import CompanionWindow

with open('config.json', 'r', encoding='utf-8') as f:
    config = json.load(f)

print('Creating CompanionWindow...')
w = CompanionWindow(config, capture_mode='loopback')
w.show()
print(f'  visible: {w.isVisible()}')
print(f'  size: {w.width()}x{w.height()}')

# Test state transitions
w.set_state('listening')
print('  state listening: OK')
w.set_state('translating')
print('  state translating: OK')
w.set_state('intense')
print('  state intense: OK')
w.set_state('sleep')
print('  state sleep: OK')
w.set_state('idle')
print('  state idle: OK')

# Test mode switch
w.set_mode('microphone')
print('  mode switch: OK')

print('All companion tests passed!')
QTimer.singleShot(300, app.quit)
app.exec()

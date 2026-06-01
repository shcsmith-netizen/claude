import re
with open('/home/opc/claude/econ_publisher.py', 'r', encoding='utf-8') as f:
    econ = f.read()

if 'executable_path=' not in econ:
    econ = re.sub(
        r'launch_persistent_context\(\s*user_data_dir=',
        'launch_persistent_context(\n                    executable_path="/usr/bin/chromium-browser",\n                    user_data_dir=',
        econ
    )
    with open('/home/opc/claude/econ_publisher.py', 'w', encoding='utf-8') as f:
        f.write(econ)
    print("✅ 발행기에 크로미움 브라우저 연결 패치 완료!")
else:
    print("✅ 이미 패치되어 있습니다.")

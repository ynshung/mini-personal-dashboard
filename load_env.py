Import("env")
import re

with open(".env") as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if re.match(r'^-?\d+$', value):
            env.Append(CPPDEFINES=[(key, value)])
        else:
            env.Append(CPPDEFINES=[(key, env.StringifyMacro(value))])

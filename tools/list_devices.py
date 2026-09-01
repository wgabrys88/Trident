import sounddevice as sd
print("=== ALL DEVICES ===")
for i, d in enumerate(sd.query_devices()):
    name = d["name"]
    in_ch = d["max_input_channels"]
    out_ch = d["max_output_channels"]
    print(f"{i:2d} | in={in_ch:2d} out={out_ch:2d} | {name}")
print()
print("=== HOST APIs ===")
for i, api in enumerate(sd.query_hostapis()):
    print(f"{i}: {api['name']} (default_in={api['default_input_device']}, default_out={api['default_output_device']})")

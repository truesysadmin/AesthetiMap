import os
import json
import glob

themes_dir = "themes"
fields = ["name", "description", "bg", "text", "gradient_color", "water", "parks", "building", "road_motorway", "road_primary", "road_secondary", "road_tertiary", "road_residential", "road_default"]

emojis = {
    "arcoiris": "🌈",
    "aurora": "🌌",
    "autumn": "��",
    "blueprint": "📐",
    "caballero_fernandez": "🇪🇸",
    "contrast_zones": "🌗",
    "copper_patina": "🗽",
    "dubai_chocolate": "🍫",
    "emerald": "✨",
    "forest": "🌲",
    "glory_to_ukraine": "🇺🇦",
    "gold_on_porcelain": "🏺",
    "gradient_roads": "🛤️",
    "japanese_ink": "��",
    "kintsugi": "🏺",
    "midnight_blue": "🌌",
    "mieres_espana": "🇪🇸",
    "monochrome_blue": "🟦",
    "neon_cyberpunk": "🌃",
    "noir": "🕵️",
    "nordic_midnight": "❄️",
    "ocean": "🌊",
    "pastel_dream": "☁️",
    "psychedelic": "🍄",
    "raven_crook": "🐦‍⬛",
    "red_on_black": "🩸",
    "ruby": "💎",
    "sunset": "🌇",
    "terracotta": "🏺",
    "unicorn": "🦄",
    "warm_beige": "☕",
    "wood": "🪵"
}

for filepath in glob.glob(os.path.join(themes_dir, "*.json")):
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    basename = os.path.basename(filepath).replace(".json", "")
    
    # Add emoji
    if basename in emojis and emojis[basename] not in data["name"]:
        data["name"] = f"{data['name']} {emojis[basename]}"
        
    # Add building field if missing
    if "building" not in data:
        # Generate a building color based on road_residential or bg
        # A simple approach: use road_residential hex or slightly offset bg
        data["building"] = data.get("road_residential", data.get("text", "#888888"))
        
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

print("Themes updated")

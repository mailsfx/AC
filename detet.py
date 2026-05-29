import easyocr
import os
import re
from PIL import Image, ImageDraw, ImageFont
from openai import OpenAI

# === НАСТРОЙКА OPENROUTER ===
API_KEY = "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"
MODEL_NAME = "openai/gpt-oss-20b:free" 
# ============================

def clean_ocr_text(text):
    if text.endswith("?I"): text = text[:-2] + "?!"
    if text.endswith("!I"): text = text[:-2] + "!!"
    if text.endswith("?l"): text = text[:-2] + "?!"
    if text.endswith("!l"): text = text[:-2] + "!!"
    return text.strip()

def clean_llm_garbage(text):
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\b(assistant|user|system|iter|output|translation|text)\b', '', text, flags=re.IGNORECASE)
    text = text.replace('"', '').replace("'", "").replace("*", "")
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'\s+([?!.,])', r'\1', text)
    return text.strip()

def group_text_by_bubbles(ocr_results, x_threshold=60, y_threshold=25):
    bubbles = []
    for bbox, text, confidence in ocr_results:
        cleaned = clean_ocr_text(text)
        if not cleaned: continue
        
        x_min = min(p[0] for p in bbox)
        x_max = max(p[0] for p in bbox)
        y_min = min(p[1] for p in bbox)
        y_max = max(p[1] for p in bbox)
        
        assigned = False
        for b in bubbles:
            x_overlap = not (x_max + x_threshold < b['left'] or x_min - x_threshold > b['right'])
            y_overlap = not (y_max + y_threshold < b['top'] or y_min - y_threshold > b['bottom'])
            
            if x_overlap and y_overlap:
                b['text'] += " " + cleaned
                if text.isupper():
                    b['is_caps'] = True
                b['top'] = min(b['top'], y_min)
                b['bottom'] = max(b['bottom'], y_max)
                b['left'] = min(b['left'], x_min)
                b['right'] = max(b['right'], x_max)
                assigned = True
                break
                
        if not assigned:
            bubbles.append({
                'text': cleaned,
                'is_caps': text.isupper(),
                'top': y_min,
                'bottom': y_max,
                'left': x_min,
                'right': x_max
            })
    return bubbles

def wrap_text(text, font, max_width):
    text = re.sub(r'\s+', ' ', text).strip()
    words = text.split(' ')
    lines = []
    current_line = []
    
    for word in words:
        if not word: continue
        test_line = ' '.join(current_line + [word]) if current_line else word
        bbox = font.getbbox(test_line)
        width = bbox[2] - bbox[0]
        
        if width <= max_width:
            current_line.append(word)
        else:
            if current_line:
                lines.append(' '.join(current_line))
            current_line = [word]
            
    if current_line:
        lines.append(' '.join(current_line))
    return lines

def translate_with_openrouter(text, is_caps):
    try:
        client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=API_KEY,
        )
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {
                    "role": "system",
                    "content": "Ты переводчик веб-комиксов. Переведи фразу на русский. Перевод должен быть ЖИВЫМ, РАЗГОВОРНЫМ и очень коротким, чтобы влезть в баббл. Выводи ТОЛЬКО перевод."
                },
                {
                    "role": "user",
                    "content": f"Переведи фразу: {text}"
                }
            ],
            temperature=0.4
        )
        raw_result = response.choices[0].message.content.strip()
        clean_result = clean_llm_garbage(raw_result)
        return clean_result.upper() if is_caps else clean_result
    except Exception as e:
        print(f"Ошибка OpenRouter: {e}")
        return text.upper() if is_caps else text

def translate_and_clean_page(image_path, output_path):
    print(f"🤖 Обработка {image_path}...")
    reader = easyocr.Reader(['en'], gpu=False)
    raw_results = reader.readtext(image_path)
    grouped_bubbles = group_text_by_bubbles(raw_results)
    
    img = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(img)
    
    # === ИЩЕМ КОМИКСНЫЙ ШРИФТ ===
    font_paths = [
        "animeace.ttf",  # Проверяем локальную папку скрипта в первую очередь!
        "AnimeAce.ttf",
        os.path.expanduser("~/.local/share/fonts/animeace.ttf"),
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"  # Фоллбэк
    ]
    font_path = None
    for path in font_paths:
        if os.path.exists(path):
            font_path = path
            break
            
    if font_path and "animeace" in font_path.lower():
        print(f"comics G is found: {font_path}")
    else:
        print("non front.")

    for bubble in grouped_bubbles:
        bubble_width = bubble['right'] - bubble['left']
        bubble_height = bubble['bottom'] - bubble['top']
        
        center_x = bubble['left'] + bubble_width / 2
        center_y = bubble['top'] + bubble_height / 2
        
        bg_x = max(0, bubble['left'] - 8)
        bg_y = int(center_y)
        bg_color = img.getpixel((bg_x, bg_y))
        
        draw.rectangle(
            [bubble['left'] - 5, bubble['top'] - 5, bubble['right'] + 5, bubble['bottom'] + 5], 
            fill=bg_color
        )
        
        translated_text = translate_with_openrouter(bubble['text'], bubble['is_caps'])
            
        target_font_size = 19  
        final_lines = []
        font = None
        max_text_width = max(75, bubble_width + 12) 
        
        # Подбираем размер, учитывая особенности комиксных шрифтов
        while target_font_size >= 9:
            if font_path:
                font = ImageFont.truetype(font_path, target_font_size)
            else:
                font = ImageFont.load_default()
                final_lines = [translated_text]
                break
                
            lines = wrap_text(translated_text, font, max_width=max_text_width)
            
            max_line_w = 0
            total_h = 0
            for line in lines:
                l_bbox = font.getbbox(line)
                max_line_w = max(max_line_w, l_bbox[2] - l_bbox[0])
                # Для Anime Ace берем чуть больше межстрочного интервала (коэффициент 1.3)
                total_h += int((l_bbox[3] - l_bbox[1]) * 1.3)
                
            if max_line_w <= max_text_width and total_h <= (bubble_height + 20):
                final_lines = lines
                break
                
            target_font_size -= 1  
            final_lines = lines

        # Отрисовка с правильным интервалом
        total_text_height = 0
        line_heights = []
        for line in final_lines:
            l_bbox = font.getbbox(line)
            h = int((l_bbox[3] - l_bbox[1]) * 1.3)
            line_heights.append(h)
            total_text_height += h
            
        current_y = center_y - (total_text_height / 2)
        
        for idx, line in enumerate(final_lines):
            l_bbox = font.getbbox(line)
            line_width = l_bbox[2] - l_bbox[0]
            x = center_x - (line_width / 2)
            
            draw.text((x, current_y), line, fill=(0, 0, 0), font=font)
            current_y += line_heights[idx]
            
        print(f"Готово: {final_lines}")
        
    img.save(output_path)
    print(f"\n done: {output_path}")

if __name__ == "__main__":
    translate_and_clean_page("test.jpg", "final_translated.jpg")
import json
import re
import pymorphy3
from typing import List, Optional, Dict, Tuple
import uvicorn
from fastapi import FastAPI, Request, Depends, HTTPException
from pydantic import BaseModel
from contextlib import asynccontextmanager


class LawLink(BaseModel):
    law_id: Optional[int] = None
    article: Optional[str] = None
    point_article: Optional[str] = None
    subpoint_article: Optional[str] = None


class LinksResponse(BaseModel):
    links: List[LawLink]


class TextRequest(BaseModel):
    text: str


class LawReferenceParser:
    def __init__(self, codex_aliases: Dict):
        self.codex_aliases = codex_aliases
        self.morph = pymorphy3.MorphAnalyzer()
        
        # ЛЕММАТИЗИРУЕМ ВСЕ названия законов из джейсона
        self.law_name_to_id = {}
        all_law_names = []
        
        for law_id, names in codex_aliases.items():
            for name in names:
                # Нормализуем название через лематизацию
                normalized_name = self.normalize_law_name(name)
                self.law_name_to_id[normalized_name] = int(law_id)
                
                # Экранируем для regex
                escaped_name = re.escape(normalized_name)
                all_law_names.append(escaped_name)
        
        # Берем ВСЕ лематизированные названия
        self.law_names_pattern = '|'.join(all_law_names)
        
        # Регулярные выражения для парсинга ссылок
        self.patterns = self._build_patterns()
    
    def normalize_law_name(self, text: str) -> str:
        """Нормализация названия закона с лематизацией"""
        if not text:
            return ""
        
        text = text.lower().strip()
        
        # Лематизация каждого слова
        words = re.findall(r'\w+', text)
        normalized_words = []
        
        for word in words:
            try:
                parsed = self.morph.parse(word)[0]
                normalized_word = parsed.normal_form
                normalized_words.append(normalized_word)
            except Exception:
                normalized_words.append(word)
        
        return ' '.join(normalized_words)
    
    def normalize_text_references(self, text: str) -> str:
        """Нормализация текста: замена полных слов на сокращения"""
        # Заменяем полные формы на сокращенные
        replacements = [
            (r'\bподпп\.', 'пп.'),
            (r'\bпункт\w*\b', 'п.'),
            (r'\bпунт\w*\b', 'п.'),
            (r'\bподпункт\w*\b', 'пп.'),
            (r'\bстать\w+\b', 'ст.'),
            (r'\bчасть\w*\b', 'ч.'),
        ]
        
        normalized_text = text
        for pattern, replacement in replacements:
            normalized_text = re.sub(pattern, replacement, normalized_text, flags=re.IGNORECASE)
        
        return normalized_text
    
    def lemmatize_full_text(self, text: str) -> str:
        """Лематизация всего текста с предварительной нормализацией ссылок"""
        # Сначала нормализуем ссылки (заменяем полные слова на сокращения)
        normalized_text = self.normalize_text_references(text)
        
        # Затем лематизируем
        tokens = re.findall(r'\w+|\W+', normalized_text)
        lemmatized_tokens = []
        
        for token in tokens:
            # Если это слово - лематизируем
            if re.match(r'^\w+$', token):
                try:
                    parsed = self.morph.parse(token)[0]
                    lemmatized_token = parsed.normal_form
                    lemmatized_tokens.append(lemmatized_token)
                except Exception:
                    lemmatized_tokens.append(token)
            else:
                # Если не слово (пробелы, знаки препинания) - оставляем как есть
                lemmatized_tokens.append(token)
        
        result = ''.join(lemmatized_tokens)
        return result
    
    def _build_patterns(self) -> List[Tuple[re.Pattern, str]]:
        """Создание регулярных выражений для поиска ссылок"""
        patterns = []
        
        # Паттерн для сложных номеров статей (с дефисами, точками и т.д.)
        article_pattern = r'[\d]+(?:[\.\-–][\d]+)*'
        
        # Основной паттерн: пп. X п. Y ст. Z Название (после нормализации)
        pattern1 = re.compile(
            r'(?:пп\.\s*([^,\s]+(?:\s*[и,]\s*[^,\s]+)*))?\s*'  # подпункты
            r'(?:п\.\s*([^,\s]+(?:\s*[и,]\s*[^,\s]+)*))?\s*'   # пункты
            rf'(?:ст\.\s*({article_pattern}))?\s*'              # статья
            fr'\s*({self.law_names_pattern})\b',                # название
            re.IGNORECASE
        )
        patterns.append((pattern1, 'pattern1'))
        
        # УПРОЩЕННЫЙ паттерн для множественных статей - избегаем катастрофического backtracking
        pattern2 = re.compile(
            rf'ст\.\s*([\d\s\,\.\-–и]+?)\s+({self.law_names_pattern})\b',
            re.IGNORECASE
        )
        patterns.append((pattern2, 'pattern2'))
        
        # Паттерн для простой статьи
        pattern3 = re.compile(
            rf'(?:ст\.\s*({article_pattern}))\s*'
            fr'\s*({self.law_names_pattern})\b',
            re.IGNORECASE
        )
        patterns.append((pattern3, 'pattern3'))
        
        return patterns
    
    def parse_reference(self, match, pattern_type: str) -> Optional[List[LawLink]]:
        """Парсинг найденной ссылки в структурированный объект"""
        try:
            # Пропускаем пустые совпадения
            if not match.group():
                return None
                
            law_links = []
            
            if pattern_type == 'pattern1':
                subpoint_text = match.group(1)
                point_text = match.group(2)
                article_text = match.group(3)
                law_name = match.group(4)
            
            elif pattern_type == 'pattern2':
                subpoint_text = None
                point_text = None
                article_text = match.group(1)
                law_name = match.group(2)
            
            elif pattern_type == 'pattern3':
                subpoint_text = None
                point_text = None
                article_text = match.group(1)
                law_name = match.group(2)
            
            # Пропускаем если нет закона
            if not law_name:
                return None
            
            # Определяем law_id
            law_id = None
            if law_name:
                normalized_law_name = law_name.lower().strip()
                law_id = self.law_name_to_id.get(normalized_law_name)
            
            # Пропускаем если не нашли закон в словаре
            if not law_id:
                return None
            
            # Очищаем основные компоненты
            article = self.clean_component(article_text) if article_text else None
            point = self.clean_component(point_text) if point_text else None
            
            # Пропускаем ссылки без полезной информации (только law_id)
            if not article and not point and not subpoint_text:
                return None
            
            # Обрабатываем множественные статьи (для pattern2)
            if pattern_type == 'pattern2' and article_text:
                articles = self.parse_multiple_articles(article_text)
                for art in articles:
                    law_link = LawLink(
                        law_id=law_id,
                        article=art,
                        point_article=None,
                        subpoint_article=None
                    )
                    law_links.append(law_link)
            # Обрабатываем множественные подпункты
            elif subpoint_text:
                subpoints = self.parse_multiple_components(subpoint_text)
                for subpoint in subpoints:
                    law_link = LawLink(
                        law_id=law_id,
                        article=article,
                        point_article=point,
                        subpoint_article=subpoint
                    )
                    law_links.append(law_link)
            else:
                law_link = LawLink(
                    law_id=law_id,
                    article=article,
                    point_article=point,
                    subpoint_article=None
                )
                law_links.append(law_link)
        
            return law_links
            
        except Exception as e:
            return None

    def parse_multiple_articles(self, articles_text: str) -> List[str]:
        """Парсинг множественных статей с сохранением сложных номеров"""
        if not articles_text:
            return []
        
        articles = re.findall(r'[\d]+(?:[\.\-–][\d]+)*', articles_text)
        return [art.strip() for art in articles if art.strip()]

    def parse_multiple_components(self, component: str) -> List[str]:
        """Парсинг множественных компонентов (пунктов, подпунктов)"""
        if not component:
            return []
        
        # Очищаем компонент
        component = self.clean_component(component)
        
        # Разделяем по запятым, "и", дефисам, но сохраняем сложные форматы
        components = re.split(r'[,и;\s]+', component)
        
        # Очищаем каждый компонент и фильтруем пустые
        cleaned_components = []
        for comp in components:
            comp = comp.strip()
            if comp and comp not in ['', 'и', ',', ';']:
                # Обрабатываем диапазоны (например, "1-3" -> ["1", "2", "3"])
                if re.match(r'^\d+\s*-\s*\d+$', comp):
                    range_parts = re.split(r'\s*-\s*', comp)
                    try:
                        start = int(range_parts[0])
                        end = int(range_parts[1])
                        for i in range(start, end + 1):
                            cleaned_components.append(str(i))
                    except:
                        cleaned_components.append(comp)
                # Обрабатываем буквенные подпункты (а, б, в)
                elif re.match(r'^[а-яa-z]$', comp, re.IGNORECASE):
                    cleaned_components.append(comp.lower())
                else:
                    cleaned_components.append(comp)
        
        return cleaned_components

    def clean_component(self, component: str) -> str:
        """Очистка компонента ссылки от лишних символов"""
        if not component:
            return None
        
        # Удаляем слова "пункт", "статья" и т.д. но сохраняем числа и буквы
        component = re.sub(r'\b(?:п|пункт|ст|статья|пп|подпункт|ч|часть)\b\.?\s*', '', component, flags=re.IGNORECASE)
        
        # Очищаем от лишних символов, но сохраняем цифры, буквы, точки, дефисы
        component = re.sub(r'[^\w\s\.\-–—]', '', component)
        component = component.strip()
        
        return component if component else None

    def filter_redundant_references(self, references: List[LawLink]) -> List[LawLink]:
        """Фильтрация избыточных ссылок - оставляем только максимально детализированные"""
        if not references:
            return []
        
        # Сортируем ссылки по уровню детализации (больше не-None полей = выше детализация)
        sorted_refs = sorted(references, key=lambda x: (
            x.subpoint_article is not None,  # С подпунктами выше (True > False)
            x.point_article is not None,     # С пунктами выше
            x.article is not None           # Со статьями выше
        ), reverse=True)
        
        filtered_references = []
        
        for ref in sorted_refs:
            is_redundant = False
            
            # Проверяем, не покрывается ли текущая ссылка уже добавленными более детальными
            for kept_ref in filtered_references:
                # Если законы разные - пропускаем
                if ref.law_id != kept_ref.law_id:
                    continue
                    
                # Если статьи разные - пропускаем
                if ref.article != kept_ref.article:
                    continue
                
                # Случай 1: если есть ссылка с тем же пунктом и подпунктом - дубликат
                if (kept_ref.point_article == ref.point_article and 
                    kept_ref.subpoint_article == ref.subpoint_article):
                    is_redundant = True
                    break
                
                # Случай 2: если есть ссылка с подпунктом для того же пункта, то ссылка только с пунктом избыточна
                if (kept_ref.point_article == ref.point_article and 
                    kept_ref.subpoint_article is not None and 
                    ref.subpoint_article is None):
                    is_redundant = True
                    break
                
                # Случай 3: если есть ссылка с пунктом для той же статьи, то ссылка только со статьей избыточна
                if (kept_ref.point_article is not None and 
                    ref.point_article is None and 
                    kept_ref.article == ref.article):
                    is_redundant = True
                    break
            
            if not is_redundant:
                filtered_references.append(ref)
        
        return filtered_references
    
    def extract_law_references(self, text: str) -> List[LawLink]:
        """Основной метод извлечения юридических ссылок из текста"""
        references = []
        
        # ЛЕММАТИЗИРУЕМ ВЕСЬ ТЕКСТ ДО ПОИСКА РЕГУЛЯРКАМИ
        lemmatized_text = self.lemmatize_full_text(text)
        
        # Очищаем текст от управляющих символов
        clean_text = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', lemmatized_text)
        
        # Ищем ссылки по всем паттернам в ЛЕММАТИЗИРОВАННОМ тексте
        for pattern, pattern_type in self.patterns:
            matches = pattern.finditer(clean_text)
            
            for match in matches:
                law_links = self.parse_reference(match, pattern_type)
                if law_links:
                    references.extend(law_links)
        
        # Удаляем дубликаты
        unique_references = []
        seen = set()
        for ref in references:
            key = (ref.law_id, ref.article, ref.point_article, ref.subpoint_article)
            if key not in seen:
                seen.add(key)
                unique_references.append(ref)
        
        # Фильтруем избыточные ссылки
        filtered_references = self.filter_redundant_references(unique_references)
        
        return filtered_references


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    try:
        with open("law_aliases.json", "r", encoding="utf-8") as file:
            codex_aliases = json.load(file)
        
        app.state.codex_aliases = codex_aliases
        app.state.parser = LawReferenceParser(codex_aliases)
        print("🚀 Сервис запускается...")
    except Exception as e:
        print(f"Ошибка запуска: {e}")
        raise
    yield
    # Shutdown
    print("🛑 Сервис завершается...")


def get_parser(request: Request) -> LawReferenceParser:
    return request.app.state.parser


app = FastAPI(
    title="Law Links Detection API",
    description="Сервис для выделения юридических ссылок из текста",
    version="1.0.0",
    lifespan=lifespan
)


@app.post("/detect")
async def get_law_links(
    data: TextRequest, 
    parser: LawReferenceParser = Depends(get_parser)
) -> LinksResponse:
    """
    Принимает текст и возвращает список юридических ссылок
    """
    try:
        # Проверяем длину текста
        if len(data.text) > 50000:
            raise HTTPException(status_code=400, detail="Text too long")
        
        # Очищаем текст от проблемных символов и экранируем кавычки
        cleaned_text = data.text.encode('utf-8', 'ignore').decode('utf-8')
        cleaned_text = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', cleaned_text)
        # Заменяем "прямые" кавычки на стандартные
        cleaned_text = cleaned_text.replace('"', '').replace('«', '').replace('»', '')
        
        references = parser.extract_law_references(cleaned_text)
        return LinksResponse(links=references)
    
    except HTTPException:
        raise
    except Exception as e:
        print(f"Ошибка обработки текста: {e}")
        return LinksResponse(links=[])


@app.get("/health")
async def health_check():
    """
    Проверка состояния сервиса
    """
    return {"status": "healthy"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8978)
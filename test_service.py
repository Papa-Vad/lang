"""
Тестовый скрипт для проверки сервиса извлечения юридических ссылок
Адаптирован для работы в Docker
"""

import json
import requests
import time
import sys
import os
from typing import List, Dict, Any
from dataclasses import dataclass

# Конфигурация для Docker
SERVICE_URL = os.getenv('SERVICE_URL', 'http://localhost:8978')
MAX_WAIT_ATTEMPTS = int(os.getenv('MAX_WAIT_ATTEMPTS', '30'))
WAIT_DELAY = float(os.getenv('WAIT_DELAY', '2.0'))
TEST_CASES_FILE = os.getenv('TEST_CASES_FILE', 'demo_test_cases.json')


@dataclass
class TestResult:
    """Результат тестирования одного случая"""
    test_case_id: int
    text: str
    expected: List[Dict[str, Any]]
    actual: List[Dict[str, Any]]
    passed: bool
    errors: List[str]


class ServiceTester:
    """Класс для тестирования сервиса"""
    
    def __init__(self, service_url: str = SERVICE_URL):
        self.service_url = service_url
        self.test_cases = []
        self.results = []
    
    def load_test_cases(self, file_path: str = TEST_CASES_FILE):
        """Загружает тестовые случаи из файла"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                self.test_cases = json.load(f)
            print(f"✅ Загружено {len(self.test_cases)} тестовых случаев из {file_path}")
        except FileNotFoundError:
            print(f"❌ Файл {file_path} не найден")
            # Попробуем найти файл в разных местах
            possible_paths = [
                '/app/' + file_path,
                './' + file_path,
                '/tests/' + file_path
            ]
            for path in possible_paths:
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        self.test_cases = json.load(f)
                    print(f"✅ Загружено {len(self.test_cases)} тестовых случаев из {path}")
                    break
                except FileNotFoundError:
                    continue
            else:
                print("❌ Не удалось найти файл с тестовыми случаями")
                sys.exit(1)
        except Exception as e:
            print(f"❌ Ошибка загрузки тестовых случаев: {e}")
            sys.exit(1)
    
    def wait_for_service(self, max_attempts: int = MAX_WAIT_ATTEMPTS, delay: float = WAIT_DELAY):
        """Ожидает готовности сервиса"""
        print(f"⏳ Ожидание готовности сервиса {self.service_url}...")
        for attempt in range(max_attempts):
            try:
                response = requests.get(f"{self.service_url}/health", timeout=5)
                if response.status_code == 200:
                    print("✅ Сервис готов к работе")
                    return True
                else:
                    print(f"⚠️  Попытка {attempt + 1}/{max_attempts}: Сервис вернул {response.status_code}")
            except requests.exceptions.ConnectionError:
                print(f"🔄 Попытка {attempt + 1}/{max_attempts}: Сервис недоступен, ждем...")
            except requests.exceptions.RequestException as e:
                print(f"⚠️  Попытка {attempt + 1}/{max_attempts}: Ошибка подключения - {e}")
            
            time.sleep(delay)
        
        print("❌ Сервис не отвечает после всех попыток")
        return False
    
    def normalize_law_link(self, link: Dict[str, Any]) -> Dict[str, Any]:
        """Нормализует ссылку для сравнения"""
        return {
            'law_id': link.get('law_id'),
            'article': link.get('article'),
            'point_article': link.get('point_article'),
            'subpoint_article': link.get('subpoint_article')
        }
    
    def compare_links(self, expected: List[Dict], actual: List[Dict]) -> tuple[bool, List[str]]:
        """Сравнивает ожидаемые и полученные ссылки"""
        errors = []
        
        # Нормализуем ссылки
        expected_norm = [self.normalize_law_link(link) for link in expected]
        actual_norm = [self.normalize_law_link(link) for link in actual]
        
        # Проверяем количество ссылок
        if len(expected_norm) != len(actual_norm):
            errors.append(f"Неверное количество ссылок: ожидалось {len(expected_norm)}, получено {len(actual_norm)}")
        
        # Проверяем каждую ожидаемую ссылку
        for i, expected_link in enumerate(expected_norm):
            if expected_link not in actual_norm:
                errors.append(f"Отсутствует ожидаемая ссылка {i+1}: {expected_link}")
        
        # Проверяем лишние ссылки
        for i, actual_link in enumerate(actual_norm):
            if actual_link not in expected_norm:
                errors.append(f"Лишняя ссылка {i+1}: {actual_link}")
        
        return len(errors) == 0, errors
    
    def test_single_case(self, test_case_id: int, text: str, expected: List[Dict]) -> TestResult:
        """Тестирует один случай"""
        try:
            # Отправляем запрос к сервису
            response = requests.post(
                f"{self.service_url}/detect",
                json={"text": text},
                timeout=30
            )
            
            if response.status_code != 200:
                return TestResult(
                    test_case_id=test_case_id,
                    text=text,
                    expected=expected,
                    actual=[],
                    passed=False,
                    errors=[f"HTTP ошибка {response.status_code}: {response.text}"]
                )
            
            actual = response.json()["links"]
            passed, errors = self.compare_links(expected, actual)
            
            return TestResult(
                test_case_id=test_case_id,
                text=text,
                expected=expected,
                actual=actual,
                passed=passed,
                errors=errors
            )
            
        except Exception as e:
            return TestResult(
                test_case_id=test_case_id,
                text=text,
                expected=expected,
                actual=[],
                passed=False,
                errors=[f"Исключение: {str(e)}"]
            )
    
    def run_all_tests(self):
        """Запускает все тесты"""
        print(f"\n🚀 Начинаем тестирование {len(self.test_cases)} случаев...")
        
        for i, test_case in enumerate(self.test_cases):
            print(f"\n📋 Тест {i+1}/{len(self.test_cases)}")
            print(f"Текст: {test_case['text'][:100]}...")
            
            result = self.test_single_case(
                test_case_id=i+1,
                text=test_case['text'],
                expected=test_case['test_cases']
            )
            
            self.results.append(result)
            
            if result.passed:
                print("✅ ПРОЙДЕН")
            else:
                print("❌ ПРОВАЛЕН")
                for error in result.errors:
                    print(f"   Ошибка: {error}")
    
    def generate_report(self):
        """Генерирует отчет о тестировании"""
        total_tests = len(self.results)
        passed_tests = sum(1 for r in self.results if r.passed)
        failed_tests = total_tests - passed_tests
        
        print("\n" + "="*80)
        print("📊 ОТЧЕТ О ТЕСТИРОВАНИИ")
        print("="*80)
        print(f"Всего тестов: {total_tests}")
        print(f"Пройдено: {passed_tests}")
        print(f"Провалено: {failed_tests}")
        success_rate = (passed_tests/total_tests)*100 if total_tests > 0 else 0
        print(f"Процент успеха: {success_rate:.1f}%")
        
        if failed_tests > 0:
            print(f"\n❌ ПРОВАЛЕННЫЕ ТЕСТЫ:")
            print("-" * 40)
            for result in self.results:
                if not result.passed:
                    print(f"\nТест {result.test_case_id}:")
                    print(f"Текст: {result.text[:150]}...")
                    print("Ошибки:")
                    for error in result.errors:
                        print(f"  - {error}")
                    print("Ожидалось:")
                    for link in result.expected:
                        print(f"  - {link}")
                    print("Получено:")
                    for link in result.actual:
                        print(f"  - {link}")
        
        # Сохраняем детальный отчет в файл
        report_data = {
            "summary": {
                "total_tests": total_tests,
                "passed_tests": passed_tests,
                "failed_tests": failed_tests,
                "success_rate": success_rate
            },
            "results": [
                {
                    "test_case_id": r.test_case_id,
                    "passed": r.passed,
                    "errors": r.errors,
                    "expected": r.expected,
                    "actual": r.actual,
                    "text": r.text
                }
                for r in self.results
            ]
        }
        
        with open("test_report.json", "w", encoding="utf-8") as f:
            json.dump(report_data, f, ensure_ascii=False, indent=2)
        
        print(f"\n📄 Детальный отчет сохранен в test_report.json")
        
        # Возвращаем код выхода в зависимости от результата
        return passed_tests == total_tests


def main():
    """Основная функция"""
    print("🧪 Тестирование сервиса извлечения юридических ссылок")
    print("=" * 60)
    
    tester = ServiceTester()
    
    # Загружаем тестовые случаи
    tester.load_test_cases()
    
    # Ждем готовности сервиса
    if not tester.wait_for_service():
        print("❌ Не удалось подключиться к сервису")
        sys.exit(1)
    
    # Запускаем тесты
    tester.run_all_tests()
    
    # Генерируем отчет и получаем результат
    all_passed = tester.generate_report()
    
    # Завершаем с соответствующим кодом
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
import re
from pathlib import Path
from typing import List, Tuple, Optional, Set, Dict
from dataclasses import dataclass

@dataclass
class CodeDefinition:
    """代码定义信息"""
    name: str
    type: str
    line_number: int
    line_content: str
    file_path: str


class CodeParser:
    """代码解析器"""
    
    # 各语言的定义模式
    PATTERNS = {
        'python': [
            (r'^class\s+(\w+)', 'class'),
            (r'^def\s+(\w+)', 'function'),
            (r'^\s+def\s+(\w+)', 'method'),
            (r'^async\s+def\s+(\w+)', 'function'),
        ],
        'javascript': [
            (r'^class\s+(\w+)', 'class'),
            (r'^function\s+(\w+)', 'function'),
            (r'^const\s+(\w+)\s*=\s*\(', 'function'),
            (r'^export\s+function\s+(\w+)', 'function'),
            (r'^export\s+class\s+(\w+)', 'class'),
        ],
        'typescript': [
            (r'^class\s+(\w+)', 'class'),
            (r'^interface\s+(\w+)', 'interface'),
            (r'^type\s+(\w+)', 'type'),
            (r'^function\s+(\w+)', 'function'),
            (r'^const\s+(\w+)\s*=\s*\(', 'function'),
            (r'^export\s+function\s+(\w+)', 'function'),
            (r'^export\s+class\s+(\w+)', 'class'),
            (r'^export\s+interface\s+(\w+)', 'interface'),
        ],
        'java': [
            (r'^public\s+class\s+(\w+)', 'class'),
            (r'^class\s+(\w+)', 'class'),
            (r'^public\s+interface\s+(\w+)', 'interface'),
            (r'^\s+public\s+\w+\s+(\w+)\s*\(', 'method'),
        ],
        'go': [
            (r'^type\s+(\w+)\s+struct', 'struct'),
            (r'^func\s+(\w+)', 'function'),
            (r'^func\s+\(\w+\s+\*?\w+\)\s+(\w+)', 'method'),
        ],
        'cpp': [
            (r'^template\s*<.*>\s*class\s+(\w+)', 'template_class'),
            (r'^template\s*<.*>\s*struct\s+(\w+)', 'template_struct'),
            (r'^namespace\s+(\w+)', 'namespace'),
            (r'^class\s+(\w+)', 'class'),
            (r'^struct\s+(\w+)', 'struct'),
            (r'^enum\s+(?:class\s+)?(\w+)', 'enum'),
            (r'^\w+(?:\s+\w+)?\s+(\w+)\s*\([^)]*\)\s*\{', 'function'),
            (r'^\s+\w+(?:\s+\w+)?\s+(\w+)\s*\([^)]*\)\s*(?:const)?\s*\{', 'method'),
        ],
        'csharp': [
            (r'^namespace\s+([\w.]+)', 'namespace'),
            (r'^\s*(?:public\s+|private\s+|protected\s+|internal\s+)?class\s+(\w+)', 'class'),
            (r'^\s*(?:public\s+|private\s+|protected\s+|internal\s+)?interface\s+(\w+)', 'interface'),
            (r'^\s*(?:public\s+|private\s+|protected\s+|internal\s+)?struct\s+(\w+)', 'struct'),
            (r'^\s*(?:public\s+|private\s+|protected\s+|internal\s+)?enum\s+(\w+)', 'enum'),
            (r'^\s+(?:public\s+|private\s+|protected\s+|internal\s+)?(?:static\s+)?(?:async\s+)?\w+(?:<[\w,\s]+>)?\s+(\w+)\s*\(', 'method'),
            (r'^\s+(?:public\s+|private\s+|protected\s+|internal\s+)?(?:\w+(?:<[\w,\s]+>)?)\s+(\w+)\s*\{\s*get', 'property'),
        ],
    }
    
    def get_file_language(self, filepath: str) -> Optional[str]:
        """根据文件扩展名判断语言"""
        ext = Path(filepath).suffix.lower()
        mapping = {
            '.py': 'python',
            '.js': 'javascript',
            '.jsx': 'javascript',
            '.ts': 'typescript',
            '.tsx': 'typescript',
            '.java': 'java',
            '.go': 'go',
            '.cpp': 'cpp',
            '.cc': 'cpp',
            '.cxx': 'cpp',
            '.c++': 'cpp',
            '.h': 'cpp',
            '.hpp': 'cpp',
            '.hxx': 'cpp',
            '.cs': 'csharp',
        }
        return mapping.get(ext)
    
    def parse_file(self, filepath: str) -> List[CodeDefinition]:
        """解析单个文件"""
        language = self.get_file_language(filepath)
        if not language or language not in self.PATTERNS:
            return []
        
        definitions = []
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                lines = f.readlines()
        except (UnicodeDecodeError, FileNotFoundError):
            return []
        
        patterns = self.PATTERNS[language]
        
        for line_num, line in enumerate(lines, 1):
            for pattern, def_type in patterns:
                match = re.search(pattern, line)
                if match:
                    definitions.append(CodeDefinition(
                        name=match.group(1),
                        type=def_type,
                        line_number=line_num,
                        line_content=line.strip(),
                        file_path=filepath
                    ))
                    break
        
        return definitions

"""
基于Tree-sitter的AST解析器
支持多语言精确提取代码结构，比正则表达式更准确高效
"""

import os
from pathlib import Path
from typing import List, Optional, Dict, Set
from dataclasses import dataclass, asdict
from typing import Any
import json

# Tree-sitter导入（可选依赖）
try:
    import tree_sitter_languages
    TREE_SITTER_AVAILABLE = True
    print(f"[AST解析器] ✓ tree-sitter可用 (版本: {tree_sitter_languages.__version__})")
except ImportError:
    TREE_SITTER_AVAILABLE = False
    print("[AST解析器] ⚠️ tree-sitter未安装，使用正则fallback")
except Exception as e:
    TREE_SITTER_AVAILABLE = False
    print(f"[AST解析器] ⚠️ tree-sitter加载失败: {e}")


@dataclass
class ASTNode:
    """AST节点信息"""
    name: str
    type: str  # 'class', 'function', 'method', 'interface', etc.
    line_number: int
    end_line: int
    line_content: str
    file_path: str
    parent: Optional[str] = None  # 父节点名称（如方法所属的类）
    params: Optional[List[str]] = None  # 函数参数
    return_type: Optional[str] = None  # 返回类型
    docstring: Optional[str] = None  # 文档字符串
    
    def to_dict(self) -> Dict:
        """转换为字典（用于序列化）"""
        return asdict(self)
    
    def to_summary(self) -> str:
        """生成简洁摘要（供LLM理解）"""
        summary = f"{self.type} {self.name}"
        if self.params:
            summary += f"({', '.join(self.params)})"
        if self.return_type:
            summary += f" -> {self.return_type}"
        if self.parent:
            summary += f" [in {self.parent}]"
        return summary


@dataclass
class ImportInfo:
    """导入语句信息"""
    source_file: str          # 导入所在文件
    imported_symbols: List[str]  # 导入的符号
    module_path: str          # 模块路径
    import_type: str          # 类型: from_import, import, require, etc.
    line_number: int          # 行号
    is_relative: bool         # 是否相对导入
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return asdict(self)


class ASTParser:
    """
    基于Tree-sitter的AST解析器
    相比正则表达式的优势：
    1. 精确解析 - 理解代码语法结构，不会误匹配
    2. 速度快 - C实现，比Python正则快数倍
    3. 丰富信息 - 提取参数、返回类型、文档字符串等
    4. 多语言统一 - 使用相同API处理所有语言
    """
    
    # 支持的语言映射
    LANGUAGE_MAP = {
        '.py': 'python',
        '.js': 'javascript',
        '.jsx': 'javascript',
        '.ts': 'typescript',
        '.tsx': 'tsx',
        '.java': 'java',
        '.go': 'go',
        '.rs': 'rust',
        '.cpp': 'cpp',
        '.cc': 'cpp',
        '.cxx': 'cpp',
        '.c': 'c',
        '.h': 'cpp',
        '.hpp': 'cpp',
        '.cs': 'c_sharp',
        '.rb': 'ruby',
        '.php': 'php',
    }
    
    # 各语言的定义查询模式
    QUERIES = {
        'python': """
            (class_definition
                name: (identifier) @class.name) @class.def
            
            (function_definition
                name: (identifier) @function.name
                parameters: (parameters) @function.params) @function.def
            
            (decorated_definition
                (function_definition
                    name: (identifier) @method.name)) @method.def
        """,
        
        'javascript': """
            (class_declaration
                name: (identifier) @class.name) @class.def
            
            (function_declaration
                name: (identifier) @function.name
                parameters: (formal_parameters) @function.params) @function.def
            
            (method_definition
                name: (property_identifier) @method.name
                parameters: (formal_parameters) @method.params) @method.def
        """,
        
        'typescript': """
            (class_declaration
                name: (type_identifier) @class.name) @class.def
            
            (interface_declaration
                name: (type_identifier) @interface.name) @interface.def
            
            (function_declaration
                name: (identifier) @function.name
                parameters: (formal_parameters) @function.params
                return_type: (type_annotation)? @function.return) @function.def
            
            (method_definition
                name: (property_identifier) @method.name
                parameters: (formal_parameters) @method.params) @method.def
        """,
        
        'java': """
            (class_declaration
                name: (identifier) @class.name) @class.def
            
            (interface_declaration
                name: (identifier) @interface.name) @interface.def
            
            (method_declaration
                name: (identifier) @method.name
                parameters: (formal_parameters) @method.params
                type: (_) @method.return) @method.def
        """,
        
        'go': """
            (type_declaration
                (type_spec
                    name: (type_identifier) @struct.name
                    type: (struct_type))) @struct.def
            
            (function_declaration
                name: (identifier) @function.name
                parameters: (parameter_list) @function.params
                result: (_)? @function.return) @function.def
            
            (method_declaration
                name: (field_identifier) @method.name
                parameters: (parameter_list) @method.params) @method.def
        """,
        
        'cpp': """
            (class_specifier
                name: (type_identifier) @class.name) @class.def
            
            (struct_specifier
                name: (type_identifier) @struct.name) @struct.def
            
            (function_definition
                declarator: (function_declarator
                    declarator: (identifier) @function.name
                    parameters: (parameter_list) @function.params)) @function.def
        """,
        
        'c_sharp': """
            (class_declaration
                name: (identifier) @class.name) @class.def
            
            (interface_declaration
                name: (identifier) @interface.name) @interface.def
            
            (method_declaration
                name: (identifier) @method.name
                parameters: (parameter_list) @method.params) @method.def
        """,
    }
    
    def __init__(self):
        self.parsers = {}  # 缓存各语言的parser
        self.available = TREE_SITTER_AVAILABLE
        
        if not self.available:
            print("[AST解析器] ⚠️ 请安装: pip install tree-sitter tree-sitter-languages")
    
    def get_parser(self, language: str):
        """获取指定语言的parser（缓存）"""
        if not self.available:
            return None
        
        if language not in self.parsers:
            try:
                # 直接导入特定语言的parser（新版API）
                if language == 'python':
                    from tree_sitter_languages import get_parser
                    self.parsers[language] = get_parser('python')
                elif language == 'javascript':
                    from tree_sitter_languages import get_parser
                    self.parsers[language] = get_parser('javascript')
                elif language == 'typescript':
                    from tree_sitter_languages import get_parser
                    self.parsers[language] = get_parser('typescript')
                else:
                    from tree_sitter_languages import get_parser
                    self.parsers[language] = get_parser(language)
            except Exception as e:
                print(f"[AST解析器] ⚠️ 无法加载语言 {language}: {e}")
                import traceback
                traceback.print_exc()
                return None
        
        return self.parsers[language]
    
    def get_language_from_file(self, filepath: str) -> Optional[str]:
        """根据文件扩展名判断语言"""
        ext = Path(filepath).suffix.lower()
        return self.LANGUAGE_MAP.get(ext)
    
    def parse_file(self, filepath: str) -> List[ASTNode]:
        """
        解析文件并提取所有定义
        
        Args:
            filepath: 文件路径
            
        Returns:
            AST节点列表
        """
        language = self.get_language_from_file(filepath)
        if not language:
            return []
        
        parser = self.get_parser(language)
        if not parser:
            return []
        
        # 读取文件内容
        try:
            with open(filepath, 'rb') as f:
                source_code = f.read()
        except (FileNotFoundError, PermissionError):
            return []
        
        # 解析为AST
        tree = parser.parse(source_code)
        
        # 获取查询模式
        query_str = self.QUERIES.get(language)
        if not query_str:
            return []
        
        # 执行查询
        try:
            from tree_sitter_languages import get_language
            lang = get_language(language)
            query = lang.query(query_str)
            captures = query.captures(tree.root_node)
        except Exception as e:
            print(f"[AST解析器] ⚠️ 查询失败 {filepath}: {e}")
            import traceback
            traceback.print_exc()
            return []
        
        # 提取节点信息
        nodes = []
        source_lines = source_code.decode('utf-8').split('\n')
        
        # 按类型组织captures - 先建立.def节点到.name的映射
        def_to_name = {}  # {def_node_id: (def_type, name, def_node)}
        name_to_info = {}  # {(def_type, name): {params, return_type}}
        
        # 第一遍：收集所有节点信息
        for node, capture_name in captures:
            parts = capture_name.split('.')
            def_type = parts[0]  # 'class', 'function', etc.
            
            if len(parts) == 2:
                if parts[1] == 'def':
                    # 完整定义节点 - 查找其中的name节点
                    for child_node, child_capture in captures:
                        if child_capture == f"{def_type}.name":
                            # 检查name节点是否是def节点的后代
                            temp = child_node
                            while temp:
                                if temp == node:
                                    # 找到了，记录映射
                                    name = child_node.text.decode('utf-8')
                                    def_to_name[id(node)] = (def_type, name, node)
                                    break
                                temp = temp.parent
                
                elif parts[1] == 'params':
                    # 参数列表 - 找到所属的name
                    parent = node.parent
                    while parent:
                        if id(parent) in def_to_name:
                            def_type_p, name_p, _ = def_to_name[id(parent)]
                            key = (def_type_p, name_p)
                            if key not in name_to_info:
                                name_to_info[key] = {}
                            name_to_info[key]['params'] = self._extract_params(node, language)
                            break
                        parent = parent.parent
                
                elif parts[1] == 'return':
                    # 返回类型
                    parent = node.parent
                    while parent:
                        if id(parent) in def_to_name:
                            def_type_p, name_p, _ = def_to_name[id(parent)]
                            key = (def_type_p, name_p)
                            if key not in name_to_info:
                                name_to_info[key] = {}
                            name_to_info[key]['return_type'] = node.text.decode('utf-8')
                            break
                        parent = parent.parent
        
        # 构建最终的definitions
        definitions = {}
        for def_node_id, (def_type, name, def_node) in def_to_name.items():
            if def_type not in definitions:
                definitions[def_type] = {}
            
            if name not in definitions[def_type]:
                key = (def_type, name)
                info = name_to_info.get(key, {})
                
                definitions[def_type][name] = {
                    'name': name,
                    'node': def_node,
                    'line': def_node.start_point[0],
                    'end_line': def_node.end_point[0],
                    'params': info.get('params'),
                    'return_type': info.get('return_type')
                }
        
        # 转换为ASTNode
        for def_type, items in definitions.items():
            for name, info in items.items():
                line_num = info['line'] + 1  # tree-sitter从0开始
                end_line = info.get('end_line', line_num) + 1
                
                # 获取第一行内容
                if line_num - 1 < len(source_lines):
                    line_content = source_lines[line_num - 1].strip()
                else:
                    line_content = ""
                
                # 提取文档字符串（如果有）
                docstring = self._extract_docstring(
                    source_lines, line_num, language
                )
                
                nodes.append(ASTNode(
                    name=name,
                    type=def_type,
                    line_number=line_num,
                    end_line=end_line,
                    line_content=line_content,
                    file_path=filepath,
                    params=info.get('params'),
                    return_type=info.get('return_type'),
                    docstring=docstring
                ))
        
        return sorted(nodes, key=lambda x: x.line_number)
    
    def _find_parent_name(self, node, captures) -> Optional[str]:
        """查找父节点的名称"""
        # 简化实现：向上查找最近的定义名称
        parent = node.parent
        while parent:
            for n, capture_name in captures:
                if n == parent and '.name' in capture_name:
                    return n.text.decode('utf-8')
            parent = parent.parent
        return None
    
    def _extract_params(self, param_node, language: str) -> List[str]:
        """提取参数列表"""
        params = []
        param_text = param_node.text.decode('utf-8')
        
        # 简单解析（去除括号和类型注解）
        if language == 'python':
            # Python: (self, name: str, age: int = 0)
            param_text = param_text.strip('()')
            for param in param_text.split(','):
                param = param.strip()
                if ':' in param:
                    param = param.split(':')[0].strip()
                if '=' in param:
                    param = param.split('=')[0].strip()
                if param and param != 'self':
                    params.append(param)
        
        elif language in ['javascript', 'typescript']:
            # JS/TS: (name, age = 0)
            param_text = param_text.strip('()')
            for param in param_text.split(','):
                param = param.strip()
                if ':' in param:
                    param = param.split(':')[0].strip()
                if '=' in param:
                    param = param.split('=')[0].strip()
                if param:
                    params.append(param)
        
        else:
            # 其他语言：简单分割
            param_text = param_text.strip('()')
            params = [p.strip() for p in param_text.split(',') if p.strip()]
        
        return params
    
    def _extract_docstring(
        self, 
        lines: List[str], 
        line_num: int, 
        language: str
    ) -> Optional[str]:
        """提取文档字符串"""
        if language == 'python':
            # Python docstring：函数下一行的三引号字符串
            if line_num < len(lines):
                next_line = lines[line_num].strip()
                if next_line.startswith('"""') or next_line.startswith("'''"):
                    # 单行docstring
                    if next_line.endswith('"""') or next_line.endswith("'''"):
                        return next_line.strip('"\' ')
                    # 多行docstring
                    docstring_lines = [next_line.strip('"\' ')]
                    for i in range(line_num + 1, min(line_num + 10, len(lines))):
                        line = lines[i].strip()
                        if line.endswith('"""') or line.endswith("'''"):
                            docstring_lines.append(line.strip('"\' '))
                            break
                        docstring_lines.append(line)
                    return ' '.join(docstring_lines)
        
        elif language in ['javascript', 'typescript']:
            # JSDoc: 函数上方的 /** */ 注释
            if line_num >= 2:
                prev_line = lines[line_num - 2].strip()
                if prev_line.startswith('/**'):
                    doc_lines = []
                    for i in range(line_num - 2, max(0, line_num - 12), -1):
                        line = lines[i].strip()
                        if line.startswith('/**'):
                            break
                        if line.startswith('*'):
                            doc_lines.insert(0, line.lstrip('* '))
                    return ' '.join(doc_lines) if doc_lines else None
        
        return None
    
    def parse_directory(
        self, 
        directory: str, 
        max_files: int = 100
    ) -> Dict[str, List[ASTNode]]:
        """
        解析目录中的所有文件
        
        Args:
            directory: 目录路径
            max_files: 最大文件数
            
        Returns:
            {文件路径: [AST节点列表]}
        """
        results = {}
        count = 0
        
        for root, dirs, files in os.walk(directory):
            # 过滤忽略目录
            dirs[:] = [d for d in dirs if d not in {
                'node_modules', '__pycache__', '.git', 'venv', 
                'dist', 'build', 'target'
            }]
            
            for file in files:
                if count >= max_files:
                    break
                
                filepath = os.path.join(root, file)
                if self.get_language_from_file(filepath):
                    nodes = self.parse_file(filepath)
                    if nodes:
                        rel_path = os.path.relpath(filepath, directory)
                        results[rel_path] = nodes
                        count += 1
        
        return results
    
    def generate_llm_context(
        self, 
        nodes: List[ASTNode], 
        include_docstring: bool = True
    ) -> str:
        """
        生成适合LLM理解的上下文摘要
        
        Args:
            nodes: AST节点列表
            include_docstring: 是否包含文档字符串
            
        Returns:
            格式化的上下文字符串
        """
        lines = []
        current_class = None
        
        for node in nodes:
            # 类定义
            if node.type in ['class', 'interface', 'struct']:
                current_class = node.name
                lines.append(f"\n{node.type.upper()} {node.name}:")
                if include_docstring and node.docstring:
                    lines.append(f"  Doc: {node.docstring}")
            
            # 函数/方法
            elif node.type in ['function', 'method']:
                indent = "  " if current_class else ""
                summary = node.to_summary()
                lines.append(f"{indent}{summary}")
                if include_docstring and node.docstring:
                    lines.append(f"{indent}  Doc: {node.docstring}")
        
        return "\n".join(lines)


# 向后兼容：提供与CodeParser相同的接口
class CodeParser:
    """兼容性包装器 - 优先使用AST，fallback到正则"""
    
    def __init__(self):
        self.ast_parser = ASTParser()
        
        # 如果tree-sitter不可用，导入正则版本
        if not self.ast_parser.available:
            from .code_parser import CodeParser as RegexCodeParser
            self.regex_parser = RegexCodeParser()
    
    def parse_file(self, filepath: str):
        """解析文件（优先AST）"""
        if self.ast_parser.available:
            nodes = self.ast_parser.parse_file(filepath)
            # 转换为旧格式（向后兼容）
            from .code_parser import CodeDefinition
            return [
                CodeDefinition(
                    name=node.name,
                    type=node.type,
                    line_number=node.line_number,
                    line_content=node.line_content,
                    file_path=node.file_path
                )
                for node in nodes
            ]
        else:
            return self.regex_parser.parse_file(filepath)

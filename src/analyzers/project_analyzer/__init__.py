"""
项目分析器模块
"""

from .file_enumerator import FileEnumerator
from .file_searcher import FileSearcher
from .fast_file_searcher import FastFileSearcher

# AST解析器
try:
    from .ast_parser import ASTParser, ASTNode, CodeParser
    AST_AVAILABLE = True
except ImportError:
    from .code_parser import CodeParser
    AST_AVAILABLE = False
    ASTParser = None
    ASTNode = None


# 导入追踪系统
try:
    from .import_tracker import (
        ImportParser,
        ModuleResolver,
        CrossFileTracker,
        ImportStatement,
        ExternalReference
    )
    IMPORT_TRACKER_AVAILABLE = True
except ImportError:
    IMPORT_TRACKER_AVAILABLE = False
    ImportParser = None
    ModuleResolver = None
    CrossFileTracker = None
    ImportStatement = None
    ExternalReference = None

__all__ = [
    'FileEnumerator',
    'CodeParser',
    'FileSearcher',
    'FastFileSearcher',
    'ASTParser',
    'ASTNode',
    'AST_AVAILABLE',
    'ImportParser',
    'ModuleResolver',
    'CrossFileTracker',
    'ImportStatement',
    'ExternalReference',
    'IMPORT_TRACKER_AVAILABLE'
]

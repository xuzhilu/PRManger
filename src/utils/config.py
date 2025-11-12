"""
配置加载工具
"""

import os
import yaml


def load_config():
    """从config.yaml加载配置"""
    # 配置文件在项目根目录的config文件夹中
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    config_path = os.path.join(project_root, 'config', 'config.yaml')
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        print(f"[ERROR] 配置文件未找到: {config_path}")
        raise
    except Exception as e:
        print(f"[ERROR] 加载配置文件失败: {str(e)}")
        raise


def load_code_rules():
    """从YAML配置文件加载代码规范"""
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    config_path = os.path.join(project_root, 'config', 'code_rules.yaml')
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
            return config.get('规范列表', [])
    except FileNotFoundError:
        print(f"[WARNING] 未找到代码规范配置文件: {config_path}")
        return []
    except Exception as e:
        print(f"[ERROR] 加载代码规范配置失败: {str(e)}")
        return []


# 加载全局配置
CONFIG = load_config()

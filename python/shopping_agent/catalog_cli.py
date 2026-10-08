"""Manage product and FAQ documents in the shopping assistant database."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence

from sqlalchemy.exc import SQLAlchemyError

from .config import Settings
from .storage import CatalogStore


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="商品与 FAQ 资料管理工具")
    commands = parser.add_subparsers(dest="command", required=True)
    import_command = commands.add_parser("import", help="从 JSONL 文件导入或更新商品")
    import_command.add_argument("path", help="每行一个商品 JSON 对象的文件")
    documents_command = commands.add_parser("import-documents", help="原子导入或更新商品与 FAQ JSONL")
    documents_command.add_argument("path")
    faq_command = commands.add_parser("import-faq", help="原子导入或更新 FAQ JSONL")
    faq_command.add_argument("path")
    delete_product_command = commands.add_parser("delete-product", help="删除商品及当前来源")
    delete_product_command.add_argument("product_id")
    delete_faq_command = commands.add_parser("delete-faq", help="删除 FAQ 及当前来源")
    delete_faq_command.add_argument("faq_id")
    source_command = commands.add_parser("source", help="按 source_id 回溯当前来源原文")
    source_command.add_argument("source_id")
    for command in (
        import_command, documents_command, faq_command,
        delete_product_command, delete_faq_command, source_command,
    ):
        command.add_argument(
            "--database-url",
            default=os.getenv("SHOPPING_DATABASE_URL", Settings().database_url),
            help="SQLAlchemy 数据库 URL；默认使用 SHOPPING_DATABASE_URL 或本地 SQLite",
        )
    args = parser.parse_args(argv)

    try:
        store = CatalogStore(args.database_url)
        store.initialize()
        if args.command == "import":
            count = store.seed_from_jsonl(args.path)
        elif args.command == "import-documents":
            count = store.import_documents_jsonl(args.path)
        elif args.command == "import-faq":
            count = store.seed_faq_from_jsonl(args.path)
        elif args.command == "delete-product":
            if not store.delete_product(args.product_id):
                raise ValueError(f"商品不存在：{args.product_id}")
            print(f"删除成功：{args.product_id}，旧来源已失效。")
            return 0
        elif args.command == "delete-faq":
            if not store.delete_faq(args.faq_id):
                raise ValueError(f"FAQ 不存在：{args.faq_id}")
            print(f"删除成功：{args.faq_id}，旧来源已失效。")
            return 0
        else:
            source = store.get_document(args.source_id)
            if source is None:
                raise ValueError(f"当前来源不存在：{args.source_id}")
            print(json.dumps({
                "source_id": source.source_id,
                "product_id": source.product_id,
                "faq_id": source.faq_id,
                "source_type": source.source_type,
                "source_url": source.source_url,
                "license": source.license,
                "updated_at": source.updated_at,
                "version": source.version,
                "original_text": source.original_text,
            }, ensure_ascii=False))
            return 0
        if count == 0:
            raise ValueError("文件中没有商品记录" if args.command == "import" else "文件中没有文档记录")
    except (OSError, ValueError, SQLAlchemyError) as exc:
        print(f"导入失败：{exc}", file=sys.stderr)
        return 1

    if args.command == "import":
        print(f"导入成功：{count} 件商品已写入数据库。")
    else:
        print(f"导入成功：{count} 份文档已写入数据库；运行中的服务需刷新检索索引。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# StuLink v1.5.0 2026-07-01
# Copyright (c) 2026 zkxxzf. CC BY-NC 4.0
from app.extensions import db


class DictCategory(db.Model):
    __tablename__ = 'dict_categories'

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), unique=True, nullable=False)
    name = db.Column(db.String(50), nullable=False)

    items = db.relationship('DictItem', backref='category', lazy='dynamic',
                            order_by='DictItem.sort_order')


class DictItem(db.Model):
    __tablename__ = 'dict_items'

    id = db.Column(db.Integer, primary_key=True)
    category_id = db.Column(db.Integer, db.ForeignKey('dict_categories.id'), nullable=False)
    value = db.Column(db.String(100), nullable=False)
    sort_order = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)

    __table_args__ = (
        db.UniqueConstraint('category_id', 'value', name='uq_category_value'),
    )

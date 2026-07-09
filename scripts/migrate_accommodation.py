import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.models import StudentAccommodation
from app.extensions import db
from sqlalchemy import text

app = create_app()

with app.app_context():
    print("开始迁移住宿数据...")
    
    count = 0
    
    result = db.session.execute(text('SELECT id, boarding_type, day_student_type, textbook, teacher_notes FROM students'))
    rows = result.fetchall()
    
    for row in rows:
        student_id, boarding_type, day_student_type, textbook, teacher_notes = row
        
        if boarding_type or day_student_type or textbook or teacher_notes:
            existing = StudentAccommodation.query.filter_by(student_id=student_id).first()
            if existing:
                existing.boarding_type = boarding_type
                existing.day_student_type = day_student_type
                existing.textbook = textbook
                existing.teacher_notes = teacher_notes
            else:
                acc = StudentAccommodation(
                    student_id=student_id,
                    boarding_type=boarding_type,
                    day_student_type=day_student_type,
                    textbook=textbook,
                    teacher_notes=teacher_notes
                )
                db.session.add(acc)
            count += 1
    
    db.session.commit()
    print(f"迁移完成，共迁移 {count} 条住宿记录")

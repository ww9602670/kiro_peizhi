import asyncio
import aiosqlite
from app.models.db_ops import operator_create

async def create_user():
    db = await aiosqlite.connect('data/bocai.db')
    db.row_factory = aiosqlite.Row
    
    # 检查用户是否已存在
    cursor = await db.execute(
        'SELECT id FROM operators WHERE username=?',
        ('e2e_final_test',)
    )
    row = await cursor.fetchone()
    
    if row:
        print(f"用户已存在: id={row['id']}")
        # 更新密码确保正确
        await db.execute(
            'UPDATE operators SET password=? WHERE username=?',
            ('test123456', 'e2e_final_test')
        )
        await db.commit()
        print("密码已更新")
    else:
        # 创建新用户
        operator_id = await operator_create(
            db,
            username='e2e_final_test',
            password='test123456',
            role='operator',
            max_accounts=10,
        )
        await db.commit()
        print(f"用户已创建: id={operator_id}")
    
    await db.close()

asyncio.run(create_user())

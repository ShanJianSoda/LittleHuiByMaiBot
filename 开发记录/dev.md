# TODO

## 增加情绪系统

参考index-tts的情绪维度：**提供一个8浮点数列表来指定每种情绪的强度，顺序如下：[快乐，愤怒，悲伤，害怕，厌恶，忧郁，惊讶，平静]** 

 `[happy, angry, sad, afraid, disgusted, melancholic, surprised, calm]`

**todo：**

- [ ] ~~优化提示词（丰富上下文 添加记忆 更真实）~~
- [ ] 用tts的八维度向量，要求：**随机初始化（有偏好）、 连续性（间隔n秒更新，有占比：聊天内容，自身状态，外界（留拓展））、 有函数约束（防止跳跃）...**
- [ ] 迭代：影响情绪的上下文（当前，相关记忆（类似的事，相关的人），先前的情绪） 
- [ ] 初步过程： 信息 -> 情绪变化 -> 影响记忆检索 -> 影响回复





步骤：

1. 初始化成功 `await mood_manager.start()`
2. 生命周期

```
1. 初始化阶段
   └─ MoodManager.start() 启动情绪回归后台任务
   └─ ChatMood 懒加载初始化（第一次使用时）

2. 情绪更新阶段（写入操作）
   └─ message_handler._preprocess_message()
      └─ chat_mood.update_mood_by_message()
         └─ 根据 interest_rate 和时间概率决定是否更新
         └─ 调用 LLM 生成新的情绪状态
         └─ 更新 self.mood_state 和 self.last_change_time

3. 情绪使用阶段（读取操作）
   ├─ plan_filter._build_prompt()
   │  └─ 读取 mood_state，影响动作决策
   │
   ├─ default_generator.build_prompt_reply_context()
   │  └─ 读取 mood_state，影响回复生成
   │
   ├─ default_generator.build_prompt_rewrite_context()
   │  └─ 读取 mood_state，影响回复重写
   │
   ├─ proactive_thinking_executor.gather_context()
   │  └─ 读取 mood_state，影响主动思考决策
   │
   └─ planner.get_mood_stats()
      └─ 读取完整情绪统计信息（用于查询）

4. 后台维护阶段
   └─ MoodRegressionTask.run()
      └─ 每30秒执行一次
      └─ 检查超过3分钟未变化的聊天流
      └─ 调用 chat_mood.regress_mood() 情绪回归
      └─ 调用 LLM 让情绪回归平静
```

3. 优化方向：

> 情绪模块开发文档.md

4. 



## 增强记忆系统

- [ ] add：将查询指定时间段消息、查询指定发言人消息、查询模糊消息内容放到工具类供AI调用
- [ ] add：增加主动思考+联网查询+存储消息





## 人物关系系统





## 心跳模块



## Filter

初版方案：bot输出后 增加一层单独的过滤器？这样可以不用重启bot（或者热部署）



# 部署

```
cd MaiBot
# 启动一个screen
screen -S mmc2
# 运行mmc
uv run python3 bot.py

cd ../MaiBot-Napcat-Adapter
screen -S mmc-adapter
# 运行adapter
uv run python3 main.py

# 俩目录都有napcat，操作失误了
cd ../../../
screen -S napcat
# 第一次运行 curl -o napcat.sh https://raw.githubusercontent.com/NapNeko/napcat-linux-installer/refs/heads/main/install.sh && bash napcat.sh
# 运行napcat
sudo bash ./launcher.sh
```



## 远程ssh

```
ssh -L 18001:127.0.0.1:8001 root@112.124.67.104
```



## 服务器同步

```
git stash push -m "tmmp"

git pull --no-rebase origin dev

git stash pop

git add . && git commit -m "update from cloud"

git push origin dev

```


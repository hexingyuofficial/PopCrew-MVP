import { useCallback, useMemo, useState } from "react";
import {
  Bot,
  Check,
  CheckCircle2,
  Circle,
  Plus,
  X,
} from "lucide-react";

type TaskStatus = 1 | 2 | 3;
type DayFilter = "yesterday" | "today" | "tomorrow";

type Task = {
  id: string;
  title: string;
  status: TaskStatus;
  /** 仅在「今日」视图展示红色顺延标签 */
  carriedOver?: boolean;
};

type CrewGroup = {
  id: string;
  name: string;
  tasks: Task[];
};

function uid(): string {
  return `t_${Math.random().toString(36).slice(2, 11)}`;
}

const INITIAL_GROUPS: CrewGroup[] = [
  {
    id: "g_xp",
    name: "制片-小胖",
    tasks: [
      {
        id: "t1",
        title: "联系犀浦废弃工厂确认电力负荷",
        status: 2,
        carriedOver: true,
      },
    ],
  },
  {
    id: "g_soda",
    name: "导演-Soda",
    tasks: [
      {
        id: "t2",
        title: "提交分镜脚本 V2 版",
        status: 3,
      },
    ],
  },
  {
    id: "g_aq",
    name: "灯光-阿强",
    tasks: [
      {
        id: "t3",
        title: "清点阿莱灯阵并装车",
        status: 1,
      },
    ],
  },
];

function nextStatus(s: TaskStatus): TaskStatus {
  if (s === 1) return 2;
  if (s === 2) return 3;
  return 1;
}

export default function App() {
  const [day, setDay] = useState<DayFilter>("today");
  const [groups, setGroups] = useState<CrewGroup[]>(() =>
    structuredClone(INITIAL_GROUPS)
  );

  const cycleTask = useCallback((groupId: string, taskId: string) => {
    setGroups((prev) =>
      prev.map((g) =>
        g.id !== groupId
          ? g
          : {
              ...g,
              tasks: g.tasks.map((t) =>
                t.id === taskId ? { ...t, status: nextStatus(t.status) } : t
              ),
            }
      )
    );
  }, []);

  const removeTask = useCallback((groupId: string, taskId: string) => {
    setGroups((prev) =>
      prev.map((g) =>
        g.id !== groupId
          ? g
          : { ...g, tasks: g.tasks.filter((t) => t.id !== taskId) }
      )
    );
  }, []);

  const addTask = useCallback((groupId: string) => {
    setGroups((prev) =>
      prev.map((g) =>
        g.id !== groupId
          ? g
          : {
              ...g,
              tasks: [
                ...g.tasks,
                {
                  id: uid(),
                  title: "新任务（演示）",
                  status: 1,
                },
              ],
            }
      )
    );
  }, []);

  const dayButtons = useMemo(
    () =>
      [
        { key: "yesterday" as const, label: "昨日" },
        { key: "today" as const, label: "今日" },
        { key: "tomorrow" as const, label: "明日" },
      ] as const,
    []
  );

  return (
    <div className="min-h-full px-5 py-6 pb-10">
      <header className="mx-auto max-w-4xl">
        <p className="text-xs font-medium uppercase tracking-widest text-zinc-400">
          Producer Dashboard · Mock
        </p>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight text-zinc-900">
          《Soda_MV》成都外景组
        </h1>
        <p className="mt-1 text-sm text-zinc-500">
          按成员分组的当日任务 · 点击卡片循环：未完成 → AI 确认 → 人类已核实
        </p>

        <div className="mt-6 flex flex-wrap items-center gap-2">
          <span className="mr-2 text-xs font-medium text-zinc-400">时间</span>
          {dayButtons.map(({ key, label }) => (
            <button
              key={key}
              type="button"
              onClick={() => setDay(key)}
              className={[
                "rounded-full px-4 py-1.5 text-sm font-medium transition-all",
                day === key
                  ? "bg-zinc-900 text-white shadow-sm"
                  : "bg-white text-zinc-600 ring-1 ring-zinc-200/80 hover:bg-zinc-50",
              ].join(" ")}
            >
              {label}
            </button>
          ))}
        </div>
      </header>

      <main className="mx-auto mt-8 max-w-4xl space-y-8">
        {groups.map((g) => (
          <section key={g.id}>
            <div className="mb-3 flex items-center justify-between gap-3">
              <h2 className="text-sm font-semibold text-zinc-800">{g.name}</h2>
              <button
                type="button"
                aria-label={`为 ${g.name} 添加任务`}
                onClick={() => addTask(g.id)}
                className="flex h-8 w-8 items-center justify-center rounded-full bg-white text-zinc-600 ring-1 ring-zinc-200 transition hover:bg-zinc-50 hover:text-zinc-900"
              >
                <Plus className="h-4 w-4" strokeWidth={2} />
              </button>
            </div>

            <ul className="space-y-2.5">
              {g.tasks.map((task) => (
                <li key={task.id}>
                  <TaskCard
                    task={task}
                    showCarriedTag={day === "today" && !!task.carriedOver}
                    onCardClick={() => cycleTask(g.id, task.id)}
                    onRemove={(e) => {
                      e.stopPropagation();
                      removeTask(g.id, task.id);
                    }}
                  />
                </li>
              ))}
            </ul>
          </section>
        ))}
      </main>
    </div>
  );
}

function TaskCard({
  task,
  showCarriedTag,
  onCardClick,
  onRemove,
}: {
  task: Task;
  showCarriedTag: boolean;
  onCardClick: () => void;
  onRemove: (e: React.MouseEvent) => void;
}) {
  const { status } = task;

  const shell =
    status === 1
      ? "border border-zinc-200/90 bg-white shadow-[0_1px_2px_rgba(0,0,0,0.04)]"
      : status === 2
        ? "border border-amber-200/80 bg-gradient-to-br from-amber-50/95 via-lime-50/60 to-white shadow-[0_1px_3px_rgba(217,119,6,0.08)]"
        : "border border-emerald-900/20 bg-emerald-800 text-white shadow-[0_4px_20px_rgba(6,78,59,0.35)]";

  const titleClass =
    status === 3 ? "text-white" : "text-zinc-900";

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onCardClick}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onCardClick();
        }
      }}
      className={[
        "group relative w-full cursor-pointer rounded-2xl px-4 py-3.5 text-left transition hover:brightness-[1.02] active:scale-[0.99]",
        shell,
      ].join(" ")}
    >
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          onRemove(e);
        }}
        className="absolute right-3 top-3 z-10 flex h-7 w-7 items-center justify-center rounded-full bg-black/5 text-zinc-500 opacity-70 transition hover:bg-black/10 hover:opacity-100"
      >
        <X className="h-3.5 w-3.5" strokeWidth={2.5} />
      </button>

      <div className="flex gap-3 pr-9">
        <div className="mt-0.5 shrink-0">
          {status === 1 && (
            <Circle className="h-5 w-5 text-zinc-300" strokeWidth={1.75} />
          )}
          {status === 2 && (
            <CheckCircle2
              className="h-5 w-5 text-amber-600"
              strokeWidth={2}
            />
          )}
          {status === 3 && (
            <span className="flex h-5 w-5 items-center justify-center rounded-full bg-white/20">
              <Check className="h-3.5 w-3.5 text-white" strokeWidth={3} />
            </span>
          )}
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            {showCarriedTag && (
              <span className="rounded-md bg-red-500 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide text-white">
                昨日顺延
              </span>
            )}
            <p className={`text-[15px] font-medium leading-snug ${titleClass}`}>
              {task.title}
            </p>
          </div>

          {status === 2 && (
            <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
              <span className="inline-flex items-center gap-1 rounded-full bg-white/70 px-2 py-0.5 font-medium text-amber-900 ring-1 ring-amber-200/60">
                <Bot className="h-3.5 w-3.5" strokeWidth={2} />
                AI 已确认
              </span>
              <span className="text-amber-800/80">· 龙虾识别</span>
              <span className="text-zinc-500">待老板终审</span>
            </div>
          )}

          {status === 3 && (
            <p className="mt-2 text-sm font-bold tracking-wide text-white/95">
              ✅ 已核实
            </p>
          )}

          {status === 1 && (
            <p className="mt-1.5 text-xs text-zinc-400">未完成 · 点击卡片推进状态</p>
          )}
        </div>
      </div>
    </div>
  );
}

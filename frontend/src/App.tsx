import {
  ApartmentOutlined,
  ApiOutlined,
  CheckCircleFilled,
  CloudServerOutlined,
  DatabaseOutlined,
  ExperimentOutlined,
  LinkOutlined,
  LogoutOutlined,
  PlayCircleFilled,
  SafetyCertificateOutlined,
  UserOutlined
} from "@ant-design/icons";
import {
  Alert,
  Badge,
  Button,
  Card,
  Col,
  Drawer,
  Empty,
  Flex,
  Form,
  Input,
  List,
  Progress,
  Row,
  Segmented,
  Space,
  Spin,
  Statistic,
  Steps,
  Switch,
  Table,
  Tag,
  Typography,
  message
} from "antd";
import type {ColumnsType} from "antd/es/table";
import {useEffect, useMemo, useState} from "react";

import {
  ApiError,
  getMe,
  getReadiness,
  getTraceReplay,
  listScenarios,
  login,
  logout,
  streamJob,
  submitTriage,
  getTriageResult
} from "./api";
import type {
  AuthUser,
  JobEvent,
  RuntimeReadiness,
  ScenarioSummary,
  TraceReplay,
  TriageResult,
  Verdict
} from "./types";

const {Title, Text, Paragraph} = Typography;

const scenarioExternalIds: Record<string, string> = {
  "TRI-001": "A-TRI-001",
  "TRI-002": "A-TRI-002",
  "TRI-003": "A-TRI-003"
};

const verdictLabels: Record<Verdict, string> = {
  true_positive: "真实威胁",
  false_positive: "业务误报",
  needs_review: "待确认",
  duplicate: "重复事件"
};

function severityTag(severity: ScenarioSummary["severity"]) {
  const colors = {low: "green", medium: "gold", high: "volcano", critical: "red"};
  return <Tag color={colors[severity]}>{severity.toUpperCase()}</Tag>;
}

function workflowStep(node: string): number {
  if (node.includes("load") || node === "queued") return 0;
  if (node.includes("plan_investigation")) return 1;
  if (
    node.includes("execute") ||
    node.includes("retrieve") ||
    node.includes("structured")
  ) {
    return 2;
  }
  if (node.includes("critic")) return 3;
  if (node.includes("replan") || node.includes("react")) return 4;
  if (node.includes("policy")) return 5;
  if (node.includes("completed")) return 6;
  return 0;
}

export default function App() {
  const [scenarios, setScenarios] = useState<ScenarioSummary[]>([]);
  const [user, setUser] = useState<AuthUser>();
  const [authLoading, setAuthLoading] = useState(true);
  const [runtime, setRuntime] = useState<RuntimeReadiness>();
  const [loading, setLoading] = useState(true);
  const [runningId, setRunningId] = useState<string>();
  const [result, setResult] = useState<TriageResult>();
  const [traceReplay, setTraceReplay] = useState<TraceReplay>();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [jobProgress, setJobProgress] = useState(0);
  const [currentNode, setCurrentNode] = useState<string>();
  const [jobEvents, setJobEvents] = useState<JobEvent[]>([]);
  const [api, contextHolder] = message.useMessage();

  useEffect(() => {
    getMe()
      .then((currentUser) => {
        setUser(currentUser);
        return Promise.all([listScenarios(), getReadiness()]);
      })
      .then((values) => {
        if (!values) return;
        const [scenarioItems, readiness] = values;
        setScenarios(scenarioItems);
        setRuntime(readiness);
      })
      .catch((error: unknown) => {
        if (!(error instanceof ApiError) || error.status !== 401) {
          api.error("无法连接后端，请检查服务状态。");
        }
      })
      .finally(() => {
        setAuthLoading(false);
        setLoading(false);
      });
  }, [api]);

  const loadConsole = async (currentUser: AuthUser) => {
    setUser(currentUser);
    setLoading(true);
    const [scenarioItems, readiness] = await Promise.all([
      listScenarios(),
      getReadiness()
    ]);
    setScenarios(scenarioItems);
    setRuntime(readiness);
    setLoading(false);
  };

  const handleLogin = async (values: {username: string; password: string}) => {
    setAuthLoading(true);
    try {
      await loadConsole(await login(values.username, values.password));
      api.success("登录成功");
    } catch {
      api.error("登录失败，请检查账号和密码。");
    } finally {
      setAuthLoading(false);
    }
  };

  const handleLogout = async () => {
    await logout();
    setUser(undefined);
    setScenarios([]);
    setResult(undefined);
  };

  const runScenario = async (scenario: ScenarioSummary) => {
    setRunningId(scenario.scenario_id);
    setResult(undefined);
    setTraceReplay(undefined);
    setJobEvents([]);
    setJobProgress(0);
    setCurrentNode("queued");
    try {
      const job = await submitTriage(scenarioExternalIds[scenario.scenario_id]);
      let terminalStatus = "";
      await streamJob(job.job_id, (event) => {
        terminalStatus = event.status;
        setJobEvents((current) => [...current.slice(-49), event]);
        setCurrentNode(event.current_node ?? undefined);
        const progress = event.data.progress;
        if (typeof progress === "number") setJobProgress(progress);
        if (["SUCCEEDED", "DEGRADED", "FAILED"].includes(event.status)) {
          setJobProgress(100);
        }
      });
      if (terminalStatus === "FAILED") throw new Error("任务执行失败");
      const triage = await getTriageResult(job.job_id);
      const replay = await getTraceReplay(job.trace_id);
      setResult(triage);
      setTraceReplay(replay);
      setDrawerOpen(true);
      api.success("研判任务已完成");
    } catch {
      api.error("任务执行失败，请检查后端状态。");
    } finally {
      setRunningId(undefined);
    }
  };

  const columns: ColumnsType<ScenarioSummary> = useMemo(
    () => [
      {
        title: "场景",
        dataIndex: "title",
        render: (_, item) => (
          <Space direction="vertical" size={2}>
            <Space>
              <Text strong>{item.title}</Text>
              {item.supports_superagent && <Tag color="cyan">SuperAgent</Tag>}
            </Space>
            <Text type="secondary">{item.description}</Text>
          </Space>
        )
      },
      {
        title: "等级",
        dataIndex: "severity",
        width: 90,
        render: severityTag
      },
      {
        title: "预期",
        dataIndex: "expected_verdict",
        width: 110,
        render: (value: Verdict) => verdictLabels[value]
      },
      {
        title: "操作",
        width: 130,
        render: (_, item) => (
          <Button
            type="primary"
            ghost
            icon={<PlayCircleFilled />}
            loading={runningId === item.scenario_id}
            onClick={() => void runScenario(item)}
          >
            智能研判
          </Button>
        )
      }
    ],
    [runningId]
  );

  if (authLoading) {
    return (
      <div className="login-shell">
        <Spin size="large" tip="正在验证本地会话…" />
      </div>
    );
  }

  if (!user) {
    return (
      <div className="login-shell">
        {contextHolder}
        <Card className="login-card">
          <Space direction="vertical" size={18} style={{width: "100%"}}>
            <div className="brand-mark login-brand">
              <SafetyCertificateOutlined />
            </div>
            <div>
              <Title level={2}>Security SuperAgent</Title>
              <Text type="secondary">登录本地安全运营控制台</Text>
            </div>
            <Form layout="vertical" onFinish={(values) => void handleLogin(values)}>
              <Form.Item
                name="username"
                label="账号"
                rules={[{required: true, message: "请输入账号"}]}
              >
                <Input prefix={<UserOutlined />} autoComplete="username" />
              </Form.Item>
              <Form.Item
                name="password"
                label="密码"
                rules={[{required: true, message: "请输入密码"}]}
              >
                <Input.Password autoComplete="current-password" />
              </Form.Item>
              <Button type="primary" htmlType="submit" block size="large">
                登录
              </Button>
            </Form>
            <Text type="secondary">会话有效期 8 小时，所有写操作均进行 CSRF 校验。</Text>
          </Space>
        </Card>
      </div>
    );
  }

  return (
    <div className="app-shell">
      {contextHolder}
      <header className="topbar">
        <Space size={14}>
          <div className="brand-mark">
            <SafetyCertificateOutlined />
          </div>
          <div>
            <Title level={4}>Security SuperAgent</Title>
            <Text type="secondary">证据优先 · 可约束 · 可审计</Text>
          </div>
        </Space>
        <Space size={18}>
          <Badge
            status={runtime?.status === "ready" ? "success" : "warning"}
            text={`${runtime?.dependencies.xdr.toUpperCase() ?? "检测中"} XDR 已连接`}
          />
          <Badge
            status="processing"
            text={`模型：${runtime?.dependencies.model ?? "检测中"}`}
          />
          <Segmented options={["运营总览", "场景编排", "知识中心"]} />
          <Tag icon={<UserOutlined />} color="blue">
            {user.username} · {user.role}
          </Tag>
          <Button icon={<LogoutOutlined />} onClick={() => void handleLogout()}>
            退出
          </Button>
        </Space>
      </header>

      <main className="content">
        <section className="hero">
          <div>
            <Text className="eyebrow">AI SECURITY OPERATIONS</Text>
            <Title>从碎片告警到受控响应闭环</Title>
            <Paragraph>
              将网络、端点、身份与知识证据合并为可验证结论，并在权限、审批和审计边界内执行响应。
            </Paragraph>
          </div>
          <Space>
            <Button icon={<ExperimentOutlined />}>加载演示数据</Button>
            <Button type="primary" icon={<PlayCircleFilled />}>
              启动主演示
            </Button>
          </Space>
        </section>

        {runningId && (
          <Card className="live-progress" size="small">
            <Flex justify="space-between" align="center" gap={16}>
              <div>
                <Text strong>实时研判进度</Text>
                <br />
                <Text type="secondary">
                  当前节点：{currentNode ?? "等待 Worker"} · 已接收 {jobEvents.length} 个审计事件
                </Text>
              </div>
              <Progress percent={jobProgress} style={{maxWidth: 420}} />
            </Flex>
          </Card>
        )}

        <Row gutter={[16, 16]}>
          <Col xs={24} md={12} xl={6}>
            <MetricCard
              title="待研判告警"
              value={scenarios.length}
              suffix="条"
              icon={<CloudServerOutlined />}
              accent="blue"
            />
          </Col>
          <Col xs={24} md={12} xl={6}>
            <MetricCard
              title="知识引用覆盖"
              value={95}
              suffix="% 目标"
              icon={<DatabaseOutlined />}
              accent="teal"
            />
          </Col>
          <Col xs={24} md={12} xl={6}>
            <MetricCard
              title="受控工具"
              value={4}
              suffix="个"
              icon={<ApiOutlined />}
              accent="orange"
            />
          </Col>
          <Col xs={24} md={12} xl={6}>
            <MetricCard
              title="未授权动作"
              value={0}
              suffix="次"
              icon={<SafetyCertificateOutlined />}
              accent="green"
            />
          </Col>
        </Row>

        <Row gutter={[16, 16]} className="main-grid">
          <Col xs={24} xl={16}>
            <Card
              title={
                <Space>
                  <ApartmentOutlined />
                  预置安全场景
                </Space>
              }
              extra={<Tag color="blue">配置驱动</Tag>}
            >
              <Spin spinning={loading}>
                <Table
                  rowKey="scenario_id"
                  columns={columns}
                  dataSource={scenarios}
                  pagination={false}
                />
              </Spin>
            </Card>
          </Col>
          <Col xs={24} xl={8}>
            <Card title="研判工作流" extra={<Switch defaultChecked checkedChildren="启用" />}>
              <Steps
                direction="vertical"
                current={currentNode ? workflowStep(currentNode) : -1}
                items={[
                  {title: "告警接入与标准化", description: "签名、去重、字段映射"},
                  {title: "Plan 调查规划", description: "网络、端点、身份、知识任务"},
                  {title: "Execute 证据执行", description: "只读工具、RAG 与结构化模型"},
                  {title: "Verify Critic 校验", description: "覆盖度、冲突和引用检查"},
                  {title: "Bounded ReAct", description: "仅证据不足时限次补证"},
                  {title: "风险与策略门", description: "dry-run、审批、幂等"},
                  {title: "执行回查与审计", description: "结果验证、Trace 回放"}
                ]}
              />
            </Card>
          </Col>
        </Row>
      </main>

      <Drawer
        width={620}
        title="研判结果"
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
      >
        {result ? (
          <ResultPanel result={result} traceReplay={traceReplay} />
        ) : (
          <Empty description="暂无结果" />
        )}
      </Drawer>
    </div>
  );
}

function MetricCard({
  title,
  value,
  suffix,
  icon,
  accent
}: {
  title: string;
  value: number;
  suffix: string;
  icon: React.ReactNode;
  accent: string;
}) {
  return (
    <Card className={`metric-card metric-${accent}`}>
      <Flex justify="space-between" align="center">
        <Statistic title={title} value={value} suffix={suffix} />
        <div className="metric-icon">{icon}</div>
      </Flex>
    </Card>
  );
}

function ResultPanel({
  result,
  traceReplay
}: {
  result: TriageResult;
  traceReplay?: TraceReplay;
}) {
  const riskColor =
    result.risk_score >= 80 ? "#ff6174" : result.risk_score >= 60 ? "#f7b955" : "#49d17d";
  return (
    <Space direction="vertical" size={20} style={{width: "100%"}}>
      <Alert
        type={result.verdict === "true_positive" ? "error" : "info"}
        showIcon
        message={verdictLabels[result.verdict]}
        description={result.summary}
      />
      <Card size="small">
        <Row gutter={20}>
          <Col span={12}>
            <Text type="secondary">风险分数</Text>
            <Progress
              type="dashboard"
              percent={result.risk_score}
              strokeColor={riskColor}
              format={(percent) => `${percent ?? 0}`}
              size={118}
            />
          </Col>
          <Col span={12}>
            <Statistic
              title="结论置信度"
              value={Math.round(result.confidence * 100)}
              suffix="%"
            />
            <Tag color={result.requires_superagent ? "cyan" : "default"}>
              {result.requires_superagent
                ? "建议升级 SuperAgent"
                : result.investigation?.replans
                  ? "混合 Agent 补证"
                  : "固定图闭环"}
            </Tag>
          </Col>
        </Row>
      </Card>
      {result.investigation && <InvestigationPanel trace={result.investigation} />}
      {traceReplay && <TraceReplayPanel replay={traceReplay} />}
      <div>
        <Title level={5}>证据链</Title>
        <List
          dataSource={result.evidence}
          renderItem={(item) => (
            <List.Item>
              <List.Item.Meta
                avatar={<CheckCircleFilled className="evidence-check" />}
                title={
                  <Space>
                    <Text>{item.source}</Text>
                    <Tag>{item.type}</Tag>
                    <Text type="secondary">{Math.round(item.confidence * 100)}%</Text>
                  </Space>
                }
                description={item.statement}
              />
            </List.Item>
          )}
        />
      </div>
      {result.citations.length > 0 && (
        <div>
          <Title level={5}>知识引用</Title>
          <List
            dataSource={result.citations}
            renderItem={(citation) => (
              <List.Item>
                <List.Item.Meta
                  avatar={<LinkOutlined className="citation-link" />}
                  title={
                    citation.source_uri.startsWith("https://") ? (
                      <a href={citation.source_uri} target="_blank" rel="noreferrer">
                        {citation.title}
                      </a>
                    ) : (
                      <Text>{citation.title}</Text>
                    )
                  }
                  description={
                    <Space direction="vertical" size={4}>
                      <Text type="secondary">
                        {citation.chunk_id} · {citation.version}
                      </Text>
                      <Text>{citation.excerpt}</Text>
                    </Space>
                  }
                />
              </List.Item>
            )}
          />
        </div>
      )}
      {result.unknowns.length > 0 && (
        <Alert
          type="warning"
          showIcon
          message="仍需确认"
          description={result.unknowns.join("；")}
        />
      )}
      {result.recommended_actions.map((action) => (
        <Card
          key={`${action.tool_name}-${action.target}`}
          size="small"
          title="受控响应建议"
          extra={<Tag color="orange">需要审批</Tag>}
        >
          <Space direction="vertical">
            <Text code>{action.tool_name}</Text>
            <Text>目标：{action.target}</Text>
            <Space>
              <Tag color="gold">{action.risk_level}</Tag>
              <Tag color="blue">{action.dry_run ? "dry-run" : "execute"}</Tag>
            </Space>
          </Space>
        </Card>
      ))}
    </Space>
  );
}

function TraceReplayPanel({replay}: {replay: TraceReplay}) {
  return (
    <Card
      size="small"
      title="SQLite Trace 回放"
      extra={<Tag color="purple">{replay.checkpoint_count} checkpoints</Tag>}
    >
      <List
        size="small"
        dataSource={replay.checkpoints}
        renderItem={(checkpoint) => (
          <List.Item>
            <List.Item.Meta
              title={
                <Space>
                  <Tag color="geekblue">step {checkpoint.step}</Tag>
                  <Text code>{checkpoint.last_node ?? "graph_input"}</Text>
                  {checkpoint.verdict && <Tag>{verdictLabels[checkpoint.verdict]}</Tag>}
                </Space>
              }
              description={
                <Text type="secondary">
                  证据 {checkpoint.evidence_count} · 引用 {checkpoint.citation_count} · ReAct
                  工具 {checkpoint.react_tool_calls} · 下一节点{" "}
                  {checkpoint.next_nodes.join("、") || "结束"}
                </Text>
              }
            />
          </List.Item>
        )}
      />
    </Card>
  );
}

function InvestigationPanel({
  trace
}: {
  trace: NonNullable<TriageResult["investigation"]>;
}) {
  const stopLabels: Record<typeof trace.stop_reason, string> = {
    not_required: "证据充分，无需 ReAct",
    evidence_acquired: "补证成功",
    evidence_exhausted: "可用证据已耗尽",
    iteration_limit: "达到迭代上限",
    tool_call_limit: "达到工具预算",
    tool_denied: "越权工具已拒绝",
    human_review_required: "转人工确认"
  };
  const agentLabels: Record<(typeof trace.plan.steps)[number]["agent"], string> = {
    network_agent: "网络调查 Agent",
    endpoint_agent: "端点调查 Agent",
    identity_agent: "身份调查 Agent",
    knowledge_agent: "知识 Agent"
  };
  return (
    <Card
      size="small"
      title="调查计划与 Agent 轨迹"
      extra={
        <Tag color={trace.critic.evidence_sufficient ? "green" : "gold"}>
          {stopLabels[trace.stop_reason]}
        </Tag>
      }
    >
      <Space direction="vertical" size={14} style={{width: "100%"}}>
        <Text>{trace.plan.objective}</Text>
        <Steps
          direction="vertical"
          size="small"
          current={-1}
          items={trace.plan.steps.map((step) => ({
            title: agentLabels[step.agent],
            description: (
              <Space direction="vertical" size={2}>
                <Text>{step.objective}</Text>
                <Text type="secondary">
                  {step.required_evidence_types.join("、") || "知识引用"} · 只读
                </Text>
              </Space>
            )
          }))}
        />
        <Alert
          type={trace.critic.evidence_sufficient ? "success" : "warning"}
          showIcon
          message="Critic 证据校验"
          description={trace.critic.rationale}
        />
        {trace.react_steps.length > 0 && (
          <div>
            <Text strong>有界 ReAct 补证</Text>
            <List
              size="small"
              dataSource={trace.react_steps}
              renderItem={(step) => (
                <List.Item>
                  <List.Item.Meta
                    title={
                      <Space>
                        <Tag color="blue">#{step.iteration}</Tag>
                        <Text code>{step.tool_name}</Text>
                        <Tag color={step.status === "completed" ? "green" : "red"}>
                          {step.status}
                        </Tag>
                      </Space>
                    }
                    description={
                      <Space direction="vertical" size={2}>
                        <Text>{step.decision_summary}</Text>
                        <Text type="secondary">{step.observation_summary}</Text>
                      </Space>
                    }
                  />
                </List.Item>
              )}
            />
          </div>
        )}
        <Text type="secondary">
          重规划 {trace.replans}/{trace.plan.max_replans} · 工具调用 {trace.tool_calls}/
          {trace.plan.max_tool_calls}
        </Text>
      </Space>
    </Card>
  );
}

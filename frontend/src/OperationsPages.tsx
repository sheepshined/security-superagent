import {
  ApiOutlined,
  CheckCircleOutlined,
  DatabaseOutlined,
  SafetyCertificateOutlined
} from "@ant-design/icons";
import {Alert, Button, Card, Col, List, Row, Space, Statistic, Steps, Tag, Typography, message} from "antd";
import {useEffect, useState} from "react";

import {
  activateWorkflowVersion,
  createWorkflowVersion,
  getKnowledgeMetrics,
  listActions,
  listKnowledgeSources,
  listTools,
  listWorkflowTemplates,
  listWorkflowVersions,
  transitionAction,
  validateWorkflowVersion
} from "./api";
import type {
  AuthUser,
  KnowledgeMetrics,
  KnowledgeSource,
  ResponseAction,
  ToolDescriptor,
  WorkflowTemplate,
  WorkflowVersion
} from "./types";

const {Title, Text, Paragraph} = Typography;

const statusColors: Record<ResponseAction["status"], string> = {
  PROPOSED: "default",
  DRY_RUN_PASSED: "blue",
  PENDING_APPROVAL: "gold",
  APPROVED: "cyan",
  REJECTED: "red",
  EXECUTING: "processing",
  SUCCEEDED: "green",
  FAILED: "red",
  VERIFIED: "success",
  ROLLED_BACK: "purple",
  EXPIRED: "default"
};

export function ApprovalCenter({user, refreshToken}: {user: AuthUser; refreshToken: number}) {
  const [actions, setActions] = useState<ResponseAction[]>([]);
  const [tools, setTools] = useState<ToolDescriptor[]>([]);
  const [busy, setBusy] = useState<string>();
  const [api, contextHolder] = message.useMessage();

  const refresh = async () => {
    const [nextActions, nextTools] = await Promise.all([listActions(), listTools()]);
    setActions(nextActions);
    setTools(nextTools);
  };

  useEffect(() => {
    void refresh().catch(() => api.error("无法加载审批中心。"));
  }, [api, refreshToken]);

  const run = async (
    action: ResponseAction,
    transition: "dry-run" | "approve" | "reject" | "execute" | "verify" | "rollback"
  ) => {
    setBusy(action.action_id);
    try {
      await transitionAction(
        action.action_id,
        transition,
        transition === "approve" ? "批准合成演示响应" : "审批中心操作"
      );
      await refresh();
      api.success("动作状态已更新");
    } catch {
      api.error("动作转换被安全策略拒绝。请检查角色、状态和审批人。" );
    } finally {
      setBusy(undefined);
    }
  };

  return (
    <Space direction="vertical" size={16} style={{width: "100%"}}>
      {contextHolder}
      <div>
        <Title level={2}>审批中心</Title>
        <Paragraph>所有高风险动作都绑定不可变计划哈希，并经过 dry-run、独立审批、模拟执行与回查。</Paragraph>
      </div>
      <Row gutter={16}>
        <Col span={8}><Card><Statistic title="待审批" value={actions.filter((item) => item.status === "PENDING_APPROVAL").length} /></Card></Col>
        <Col span={8}><Card><Statistic title="可审计动作" value={actions.length} /></Card></Col>
        <Col span={8}><Card><Statistic title="注册工具" value={tools.length} /></Card></Col>
      </Row>
      <Card title="响应动作队列" extra={<Tag color="blue">Mock Response Adapter</Tag>}>
        <List
          locale={{emptyText: "完成一次真实威胁研判后，可从结果中创建响应计划。"}}
          dataSource={actions}
          renderItem={(action) => {
            const independentReviewer =
              action.requested_by !== user.user_id && ["admin", "approver"].includes(user.role);
            return (
              <List.Item
                actions={[
                  action.status === "PROPOSED" && <Button key="dry" onClick={() => void run(action, "dry-run")}>Dry-run</Button>,
                  action.status === "PENDING_APPROVAL" && independentReviewer && <Button key="approve" type="primary" onClick={() => void run(action, "approve")}>批准</Button>,
                  action.status === "PENDING_APPROVAL" && independentReviewer && <Button key="reject" danger onClick={() => void run(action, "reject")}>拒绝</Button>,
                  action.status === "APPROVED" && independentReviewer && <Button key="execute" type="primary" loading={busy === action.action_id} onClick={() => void run(action, "execute")}>模拟执行</Button>,
                  action.status === "SUCCEEDED" && <Button key="verify" onClick={() => void run(action, "verify")}>执行回查</Button>,
                  ["SUCCEEDED", "VERIFIED"].includes(action.status) && independentReviewer && <Button key="rollback" onClick={() => void run(action, "rollback")}>回滚</Button>
                ].filter(Boolean)}
              >
                <List.Item.Meta
                  avatar={<SafetyCertificateOutlined />}
                  title={<Space><Text code>{action.tool_name}</Text><Tag color={statusColors[action.status]}>{action.status}</Tag><Tag>{action.risk_level}</Tag></Space>}
                  description={
                    <Space direction="vertical" size={3}>
                      <Text>目标：{action.target} · 发起人：{action.requested_by_username}</Text>
                      <Text>{action.reason}</Text>
                      <Text type="secondary">计划哈希：{action.plan_hash.slice(0, 20)}… · 到期：{new Date(action.expires_at).toLocaleString()}</Text>
                      {action.requested_by === user.user_id && action.status === "PENDING_APPROVAL" && <Alert type="info" showIcon message="发起人不能批准自己的动作，请使用独立审批员账号。" />}
                    </Space>
                  }
                />
              </List.Item>
            );
          }}
        />
      </Card>
    </Space>
  );
}

export function KnowledgeCenter() {
  const [metrics, setMetrics] = useState<KnowledgeMetrics>();
  const [sources, setSources] = useState<KnowledgeSource[]>([]);

  useEffect(() => {
    void Promise.all([getKnowledgeMetrics(), listKnowledgeSources()]).then(([nextMetrics, nextSources]) => {
      setMetrics(nextMetrics);
      setSources(nextSources);
    });
  }, []);

  return (
    <Space direction="vertical" size={16} style={{width: "100%"}}>
      <div><Title level={2}>知识中心</Title><Paragraph>来源、许可、版本、访问标签和活动索引均可追溯。</Paragraph></div>
      <Row gutter={16}>
        <Col span={6}><Card><Statistic title="活动索引" value={metrics?.active_index_id ? 1 : 0} prefix={<DatabaseOutlined />} /></Card></Col>
        <Col span={6}><Card><Statistic title="文档" value={metrics?.document_count ?? 0} /></Card></Col>
        <Col span={6}><Card><Statistic title="分块" value={metrics?.chunk_count ?? 0} /></Card></Col>
        <Col span={6}><Card><Statistic title="来源" value={metrics?.source_count ?? 0} /></Card></Col>
      </Row>
      <Alert type={metrics?.chroma_enabled ? "success" : "warning"} showIcon message={metrics?.chroma_enabled ? `Chroma + ONNX 已启用：${metrics.active_index_id}` : "当前使用离线快照回退"} />
      <Card title="登记来源">
        <List dataSource={sources} renderItem={(source) => <List.Item><List.Item.Meta avatar={<DatabaseOutlined />} title={<Space><Text>{source.name}</Text><Tag>{source.version}</Tag><Tag color={source.enabled ? "green" : "default"}>{source.enabled ? "启用" : "停用"}</Tag></Space>} description={<Space direction="vertical" size={2}><Text type="secondary">{source.source_id} · {source.source_type} · {source.license}</Text><a href={source.uri} target="_blank" rel="noreferrer">{source.uri}</a></Space>} /></List.Item>} />
      </Card>
    </Space>
  );
}

export function WorkflowCenter({user}: {user: AuthUser}) {
  const [templates, setTemplates] = useState<WorkflowTemplate[]>([]);
  const [versions, setVersions] = useState<WorkflowVersion[]>([]);
  const [busy, setBusy] = useState(false);
  const [api, contextHolder] = message.useMessage();

  const refresh = async () => {
    const [nextTemplates, nextVersions] = await Promise.all([listWorkflowTemplates(), listWorkflowVersions()]);
    setTemplates(nextTemplates);
    setVersions(nextVersions);
  };
  useEffect(() => { void refresh(); }, []);

  const createDefault = async () => {
    const template = templates[0];
    if (!template) return;
    setBusy(true);
    try {
      const version = await createWorkflowVersion(template.template_id, template.default_config);
      const validation = await validateWorkflowVersion(version.workflow_version_id);
      if (!validation.valid) throw new Error(validation.errors.join("; "));
      await activateWorkflowVersion(version.workflow_version_id);
      await refresh();
      api.success("工作流版本已校验并激活");
    } catch {
      api.error("工作流版本创建或校验失败");
    } finally {
      setBusy(false);
    }
  };

  const template = templates[0];
  return (
    <Space direction="vertical" size={16} style={{width: "100%"}}>
      {contextHolder}
      <div><Title level={2}>模板化工作流</Title><Paragraph>可调模型、预算、Agent、工具和知识范围；Critic、策略裁决与响应安全门不可删除。</Paragraph></div>
      {template && <Row gutter={16}><Col span={15}><Card title={template.name} extra={<Tag color="green">固定安全拓扑</Tag>}><Steps items={[...template.fixed_nodes, ...template.configurable_nodes].map((node) => ({title: node, icon: template.fixed_nodes.includes(node) ? <SafetyCertificateOutlined /> : <ApiOutlined />}))} direction="vertical" size="small" /></Card></Col><Col span={9}><Card title="默认预算"><Space direction="vertical"><Text>模型：{template.default_config.model}</Text><Text>节点超时：{template.default_config.node_timeout_seconds}s</Text><Text>工具上限：{template.default_config.max_tool_calls}</Text><Text>重规划：{template.default_config.max_replans}</Text><Text>Agent：{template.default_config.agents.length}</Text>{user.role === "admin" && <Button type="primary" icon={<CheckCircleOutlined />} loading={busy} onClick={() => void createDefault()}>创建、校验并激活</Button>}</Space></Card></Col></Row>}
      <Card title="不可变版本"><List dataSource={versions} locale={{emptyText: "尚未保存工作流版本"}} renderItem={(version) => <List.Item><List.Item.Meta title={<Space><Text>v{version.version_number}</Text><Tag color={version.status === "active" ? "green" : "blue"}>{version.status}</Tag></Space>} description={<Text type="secondary">{version.workflow_version_id} · {version.config_hash.slice(0, 20)}…</Text>} /></List.Item>} /></Card>
    </Space>
  );
}

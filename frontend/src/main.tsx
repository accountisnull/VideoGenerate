import { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Alert, Button, Card, ConfigProvider, Flex, List, Tag, Typography } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import 'antd/dist/reset.css';
import './style.css';

type Health = {
  api: string; database: string; worker: string;
  media: Record<string, boolean>; blockers: string[];
};
type Check = { id: string; state: string; detail: string; created_at: number };
const labels: Record<string, string> = {
  queued: '等待执行', running: '正在自检', succeeded: '自检通过', failed: '自检失败',
};

async function request<T>(url: string, method = 'GET'): Promise<T> {
  const response = await fetch(url, { method });
  if (!response.ok) {
    const body = await response.json();
    throw new Error(body.detail || `请求失败：${response.status}`);
  }
  return response.json();
}

function App() {
  const [health, setHealth] = useState<Health>();
  const [checks, setChecks] = useState<Check[]>([]);
  const [error, setError] = useState('');
  const [starting, setStarting] = useState(false);
  async function refresh() {
    try {
      const [status, history] = await Promise.all([
        request<Health>('/api/health'), request<Check[]>('/api/checks'),
      ]);
      setHealth(status); setChecks(history); setError('');
    } catch (e) { setError(String(e)); }
  }
  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => { void refresh(); }, 2000);
    return () => window.clearInterval(timer);
  }, []);
  async function start() {
    setStarting(true);
    try { await request('/api/checks', 'POST'); await refresh(); }
    catch (e) { setError(String(e)); }
    finally { setStarting(false); }
  }
  const latest = checks[0];
  const busy = checks.some(check => ['queued', 'running'].includes(check.state));
  return <ConfigProvider locale={zhCN} theme={{ token: { colorPrimary: '#225e58', borderRadius: 12 } }}>
    <main>
      <Typography.Text className="eyebrow">VIDEO GENERATE / LOCAL</Typography.Text>
      <Typography.Title>视频生成 · 本机联调</Typography.Title>
      <Typography.Paragraph type="secondary">验证本机环境，逐步接通内容、配音、数字人和抖音发布。</Typography.Paragraph>
      {error && <Alert type="error" title={error} showIcon />}
      <Flex gap={16} wrap className="status">
        {[
          ['API', health?.api === 'ok'], ['SQLite', health?.database === 'ok'],
          ['独立 Worker', health?.worker === 'online'],
          ['FFmpeg / ffprobe', !!health?.media.ffmpeg && !!health?.media.ffprobe],
        ].map(([name, ready]) => <Card key={String(name)} size="small">
          <Typography.Text strong>{name}</Typography.Text><br />
          <Tag color={ready ? 'success' : 'default'}>{ready ? '已就绪' : '未就绪'}</Tag>
        </Card>)}
      </Flex>
      <div className="columns">
        <Card title="环境自检" extra={<Tag>不调用付费 API</Tag>}>
          <Typography.Paragraph>由独立 Worker 生成 2 秒测试图案与测试音，合成 MP4 并检查音视频流。此测试不生成数字人，也不会上传到抖音。</Typography.Paragraph>
          <Button type="primary" loading={starting || busy} disabled={health?.worker !== 'online'} onClick={() => void start()}>运行环境自检</Button>
          {latest && <div className="result">
            <Tag color={latest.state === 'succeeded' ? 'success' : latest.state === 'failed' ? 'error' : 'processing'}>{labels[latest.state] || latest.state}</Tag>
            <Typography.Paragraph>{latest.detail}</Typography.Paragraph>
            {latest.state === 'succeeded' && <video key={latest.id} controls preload="metadata" aria-label="环境自检测试片" src={`/api/checks/${latest.id}/video`} />}
          </div>}
        </Card>
        <Card title="真实生产接入状态">
          <Alert type="info" title="真实生成与发布尚未接通" showIcon />
          <List dataSource={health?.blockers || []} renderItem={item => <List.Item>{item}</List.Item>} />
          <Typography.Text type="secondary">当前验证范围：页面 → API → SQLite → Worker → FFmpeg → 视频预览。</Typography.Text>
        </Card>
      </div>
    </main>
  </ConfigProvider>;
}

createRoot(document.getElementById('root')!).render(<App />);

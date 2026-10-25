import React from 'react';
import ReactDOM from 'react-dom/client';
import { App as AntApp, ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import dayjs from 'dayjs';
import 'dayjs/locale/zh-cn';
import App from './App';
import './shared/styles/tokens.css';
import './shared/styles/globals.css';
import './shared/styles/layout.css';
import './styles.css';

dayjs.locale('zh-cn');
// Ant Design 与布局样式读取同一份 Token，避免主题色和尺寸分别维护。
const styles = getComputedStyle(document.documentElement);
const token = (name: string) => styles.getPropertyValue(`--${name}`).trim();
ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ConfigProvider locale={zhCN} theme={{ token: {
      colorPrimary: token('color-primary'), colorInfo: token('color-primary'),
      colorText: token('color-text'), colorTextSecondary: token('color-text-secondary'),
      colorBorder: token('color-border'), colorBgLayout: token('color-background'),
      borderRadius: parseFloat(token('radius-md')), fontFamily: token('font-family'),
      controlHeight: parseFloat(token('control-height')),
    } }}>
      <AntApp><App /></AntApp>
    </ConfigProvider>
  </React.StrictMode>,
);

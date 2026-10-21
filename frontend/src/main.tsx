import React from 'react';
import ReactDOM from 'react-dom/client';
import { App as AntApp, ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import dayjs from 'dayjs';
import 'dayjs/locale/zh-cn';
import App from './App';
import './styles.css';

dayjs.locale('zh-cn');
ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ConfigProvider locale={zhCN} theme={{ token: {
      colorPrimary: '#127568', colorInfo: '#127568', colorText: '#243331', colorTextSecondary: '#76827e',
      colorBorder: '#dce5e1', colorBgLayout: '#f5f7f6', borderRadius: 10,
      fontFamily: 'Inter, "Segoe UI", "Microsoft YaHei", sans-serif', controlHeight: 40,
    } }}>
      <AntApp><App /></AntApp>
    </ConfigProvider>
  </React.StrictMode>,
);

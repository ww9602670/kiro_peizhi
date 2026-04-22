import { operatorUpdates } from '@/data/operatorUpdates';
import './PlatformUpdates.css';

export default function PlatformUpdates() {
  return (
    <div className="platform-updates-page">
      <h1 className="platform-updates-title">平台更新</h1>
      <p className="platform-updates-subtitle">
        这里持续记录和日常操作相关的功能变化。以后每次有新功能或修复，都会先更新这里。
      </p>

      {operatorUpdates.length > 0 ? (
        <div className="platform-updates-list">
          {operatorUpdates.map((item) => (
            <article key={item.id} className="platform-update-card">
              <div className="platform-update-meta">
                <time dateTime={item.date} className="platform-update-date">{item.date}</time>
              </div>
              <h2 className="platform-update-card-title">{item.title}</h2>
              <ul className="platform-update-items">
                {item.items.map((detail) => (
                  <li key={detail} className="platform-update-item">{detail}</li>
                ))}
              </ul>
            </article>
          ))}
        </div>
      ) : (
        <p className="empty-text">暂无更新内容</p>
      )}
    </div>
  );
}

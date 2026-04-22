import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import PlatformUpdates from './PlatformUpdates';
import { operatorUpdates } from '@/data/operatorUpdates';

describe('PlatformUpdates', () => {
  it('renders update cards from the shared markdown feed', () => {
    render(<PlatformUpdates />);

    expect(screen.getByRole('heading', { level: 1, name: '平台更新' })).toBeInTheDocument();
    expect(screen.getAllByRole('article')).toHaveLength(operatorUpdates.length);

    operatorUpdates.forEach((item) => {
      expect(screen.getByText(item.title)).toBeInTheDocument();
      expect(screen.getAllByText(item.date).length).toBeGreaterThan(0);

      item.items.forEach((detail) => {
        expect(screen.getByText(detail)).toBeInTheDocument();
      });
    });
  });

  it('shows the current latest update summary', () => {
    render(<PlatformUpdates />);

    expect(screen.getByText(operatorUpdates[0].title)).toBeInTheDocument();
    expect(screen.getByText(operatorUpdates[0].items[0])).toBeInTheDocument();
  });
});

import { create } from 'zustand';

interface UIState {
  accountModalOpen: boolean;
  openAccountModal: () => void;
  closeAccountModal: () => void;
}

export const useUIStore = create<UIState>((set) => ({
  accountModalOpen: false,
  openAccountModal: () => set({ accountModalOpen: true }),
  closeAccountModal: () => set({ accountModalOpen: false }),
}));

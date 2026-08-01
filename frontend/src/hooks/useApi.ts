import { useEffect, useRef, useState } from "react";

interface ApiState<T> {
  data: T;
  loading: boolean;
  error: string | null;
}

export function useApi<T>(loader: () => Promise<T>, initial: T): ApiState<T> {
  const initialRef = useRef(initial);
  const [state, setState] = useState<ApiState<T>>({
    data: initialRef.current,
    loading: true,
    error: null,
  });

  useEffect(() => {
    let active = true;
    loader()
      .then((data) => {
        if (active) setState({ data, loading: false, error: null });
      })
      .catch((error: unknown) => {
        if (active) {
          setState({
            data: initialRef.current,
            loading: false,
            error: error instanceof Error ? error.message : "请求失败",
          });
        }
      });
    return () => {
      active = false;
    };
  }, [loader]);

  return state;
}

port P

active component C {

  async input port p: P priority 32

}

instance c: C base id 0x100 \
  queue size 10 \
  queue priorities {
    priority 32 size 5
  } \
  stack size 10 * 1024 \
  priority 3

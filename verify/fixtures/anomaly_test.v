`timescale 1ns/1ps

module anomaly_test;
    reg clk;
    reg rst_n;

    // Normal signal
    reg data_valid;

    // Stuck signal (will not change for a long time)
    reg stuck_signal;

    // Glitch signal (very short pulse)
    reg glitch_signal;

    // Metastability signal (will have x values)
    reg sync_flag;

    // Bus contention signal
    reg [7:0] data_bus;

    initial begin
        $dumpfile("anomaly_basic.vcd");
        $dumpvars(0, anomaly_test);

        clk = 0;
        rst_n = 0;
        data_valid = 0;
        stuck_signal = 0;
        glitch_signal = 0;
        sync_flag = 0;
        data_bus = 8'h00;

        // Reset
        #20 rst_n = 1;

        // Normal activity
        #20 data_valid = 1;
        #20 data_valid = 0;
        #20 data_valid = 1;
        #20 data_valid = 0;

        // Glitch: very short pulse (2ns)
        #20 glitch_signal = 1;
        #2  glitch_signal = 0;

        // Normal pulse for comparison (20ns)
        #20 glitch_signal = 1;
        #20 glitch_signal = 0;

        // Stuck signal starts here, will stay at 1 for a long time
        #20 stuck_signal = 1;

        // Metastability: x value
        #20 sync_flag = 1'bx;
        #10 sync_flag = 0;

        // More normal activity
        #20 data_valid = 1;
        #20 data_valid = 0;

        // Bus contention: all x values
        #20 data_bus = 8'hxx;
        #20 data_bus = 8'h00;

        // Another glitch
        #20 glitch_signal = 1;
        #1  glitch_signal = 0;

        // Continue with stuck_signal = 1 (totally stuck for >300ns)
        #500;

        // Finally, stuck_signal releases
        stuck_signal = 0;

        #50 $finish;
    end

    always #5 clk = ~clk;

endmodule
